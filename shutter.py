import logging
import yaml
import boto3
import re
from os import path
from datetime import datetime
from concurrent import futures
from dateutil.relativedelta import relativedelta
from requests.utils import CaseInsensitiveDict

# the prefix tag for config settings on instances and snapshots
SETTING_TAG = "Shutter-"

logging.basicConfig(level=logging.INFO, filename="shutter.log",
                    format="%(asctime)s - %(name)s [%(levelname)s] - %(message)s",
                    datefmt='%m/%d/%Y %H:%M:%S')

log = logging.getLogger(__name__)


class Instance(CaseInsensitiveDict):
    """
    Instance objects store configs in a case insensitive dictionary and the 
    instance and region as attributes. Config attributes are set from defaults
    passed to __init__ and from instance tags (in that order).

    :type instance: ec2.Instance
    :param instance: ec2 instance object
    :type region: str
    :param region: region that the instance is in
    :type defaults: dict
    :param defaults: dictionary to initialize instance config options
    """

    def __init__(self, instance, region, defaults={}):
        super(Instance, self).__init__(defaults)
        self.region = region
        self.instance = instance

        conf_tags = { k[k.startswith(SETTING_TAG) and len(SETTING_TAG):]: v.lower() for k, v in self.tags.items() if re.match(SETTING_TAG+"*", k) }

        for k, v in conf_tags.items():
            # keep types consistent for overriden config options
            if self.get(k, None) != None:
                if isinstance(self[k], bool):
                    if v.lower() in ["true", "yes"]:
                        conf_tags[k] = True
                    else:
                        conf_tags[k] = False
                else:
                    conf_tags[k] = type(self[k])(v)

        self.update(conf_tags)

    @property
    def name(self):
        return self.tags["Name"]

    @property
    def tags(self):
        return {t["Key"]: t["Value"] for t in self.instance.tags}

    def __repr__(self):
        return "<{} in {}>".format((self.name or self.instance.id), self.region)

    def getVolume(self, volume):
        """
        Queries EC2 for a volume by name

        :type device: str
        :param device: The requested device name (ex. /dev/sda1)

        :rtype: ec2.Volume
        :return: The requested volume, or None
        """
        q = list(self.instance.volumes.filter(
            Filters=[
                {"Name": "attachment.device",
                 "Values": [volume]}
            ]
        ))
        return q[0] if len(q) else None

    def getVolumeSnapshots(self, volume, status=None):
        """
        Queries EC2 for a list of snapshots for a given device

        :type device: ec2.Volume
        :param device: The EC2 volume to get the snapshots for
        :type status: str
        :param status: Optional snapshot status. One of ["pending", "completed"]

        :rtype: list
        :return: a list of snapshots for a given device
        """
        if isinstance(volume, str):
            volume = self.getVolume(volume)
        if not volume:
            return []
        if status:
            return list(volume.snapshots.filter(
                Filters=[
                    {"Name": "status",
                     "Values": [status]}
                ]
            ))
        else:
            return list(volume.snapshots.all())

    def getRootVolumeSnapshots(self):
        """
        Retrieves and sorts device snapshots for an instance by start time

        :rtype: list
        :return: list of snapshots for the root volume of the given EC2 instance
        """
        devname = self.get('rootdevice')
        s = self.getVolumeSnapshots(devname)
        s.sort(key=lambda i: i.meta.data["StartTime"])
        return s

    def snapshot(self, desc=None, tags=None):
        """
        Snapshots the given instance's root volume

        :type desc: str
        :param desc: description to assign to the snapshot
        :type tags: str
        :param tags: tags to assign to the snapshot

        :rtype ec2.Snapshot
        :return: The snapshot if one is taken or None
        """
        if tags:
            tags = [{'Key': k, 'Value': v} for k, v in tags.items()]
            ts = [{'ResourceType': 'snapshot', "Tags": tags}]
        devname = self.get('rootdevice')
        volume = self.getVolume(devname)
        if not volume:
            log.error("Volume {} not found for instance").format(devname, self.name)
            return None
        else:
            return volume.create_snapshot(Description=desc, TagSpecifications=ts)


class Shutter(object):
    """
    The shutter object gets configs and instances from files and provides
    snapshot management tools based on those configs and instances.

    :type config_file: string
    :param config_file: the path to the config file
    """
    def __init__(self, config_file):

        if not config_file:
            raise Exception("No config file specified.")

        self.ec2 = dict()
        self.loadConfig(config_file)

        # Default log level is INFO (from above)
        loglevel = getattr(logging, self.config.get("loglevel", "INFO").upper())
        if isinstance(loglevel, int):
            log.setLevel(loglevel)
        else:
            log.warning("LogLevel config option ({}) is invalid, defaulting to INFO".format(self.config.get("LogLevel")))

        regions = self.config.get("Regions")
        self.profile = self.config.get("AWSProfile", "default")
        self.session = boto3.Session(profile_name=self.profile)
        for r in regions:
            self.initRegion(r)
        self.populateInstances()

    def populateInstances(self):
        """
        Get instances that are shutter enabled and store them into an attribute
        """
        self.instances = []
        # some leeway in case
        filt = lambda x: x['Key'] == SETTING_TAG+"Enable" and x['Value'].lower() in ['true', 'yes']
        for region, session in self.ec2.items():
            instances = list(session.instances.filter(Filters=[{"Name": "tag:{}Enable".format(SETTING_TAG), "Values": ["*"]}]))
            for i in instances:
                if filter(filt, i.tags):
                    self.instances.append(Instance(i, region, self.config["Default"]))

    def loadConfig(self, config_file):
        """
        Uses the yaml parser to import configuration options into the object

        :type config_file: string
        :param config_file: The yaml file containing configuration details

        :rtype: boolean
        :return: False if the file does not exist, True otherwise
        """
        if not path.exists(config_file):
            log.error("{} not found".format(config_file))
            return False
        # TODO: handle issues opening or reading the file
        with open(config_file) as f:
            self.config = yaml.load(f.read())
        return True

    def initRegion(self, region):
        """
        Initialize an EC2 region object and add it to the region list.
        More often than not this list will of be of size one, but this way
        shutter can be run across multiple regions by specifying in the instance
        configuration

        :type region: string
        :param region: the region name to initialize
        """
        if self.ec2.get(region, None):
            log.debug("Region {} has already been initialized".format(region))
            return
        self.ec2[region] = self.session.resource('ec2', region_name=region)

    def pruneSnapshots(self, snapshots, histsize):
        """
        Identifies and deletes old snapshots based on a history size. Only deletes
        snapshots managed by Shutter

        :type snapshots: list(ec2.Snapshots)
        :param snapshots: list of snapshots to prune, based on histsize
        :type histsize: int
        :param histsize: the number of snapshots that should be kept

        :rtype: int
        :return: 
        """
        deleted = 0
        if len(snapshots) > histsize:
            to_delete = snapshots[:histsize-1]
            for snap in to_delete:
                log.debug("Deleting snapshot " + snap.id)
                deleted += 1
                snap.delete()
        return deleted

    def run(self, concurrent=True):
        """
        For all valid instances from the instances file, check if a new snapshot
        needs to be created and also prune old snapshots if required
        """
        if concurrent:
            with futures.ThreadPoolExecutor(max_workers=10) as e:
                for i in self.instances:
                    e.submit(self.runOne, i)
        else:
            for i in self.instances:
                self.runOne(i)

    def runOne(self, instance):
        """
        For a single instance in the instances file, check if a new snapshot
        needs to be created and also prune old snapshots if required

        :type instance: dict
        :param instance: Contains the ec2.Instance object as well as config data
                         from the instances file
        """
        snap = self.snapshotInstanceWithFrequency(instance)
        prune = instance.get("deleteoldsnapshots")
        if instance["offsitebackup"] and snap:
            self.makeOffsiteSnapshotWithFrequency(instance, snap)
        if prune:
            # prune main snapshots
            snapshots = instance.getRootVolumeSnapshots()
            histsize = instance.get("historysize")
            self.pruneSnapshots(snapshots, histsize)

            if instance["offsitebackup"]:
                snapshots = self.getInstanceOffsiteBackupSnapshots(instance)
                histsize = instance.get("offsitehistorysize")
                self.pruneSnapshots(snapshots, histsize)

    @staticmethod
    def _timeWithinFrequency(time, frequency, jitter_minutes=10):
        """
        See if the time is within an acceptable named period with jitter. 
        Takes the current time and checks it against the past time plus 
        frequency and jitter.

        :type time: datetime.datetime
        :param time: base time object
        :type frequency: str
        :param frequency: period of time. one of ["daily", "weekly", monthly"]
        :type jitter_minutes: int
        :param jitter_minutes: number of minutes of leeway to give

        :rtype: bool
        :return: True if the time object is within the frequency, False otherwise
        """
        # TODO: refactor so it makes more programmatic sense
        frequency = frequency.lower()
        if frequency == 'daily':
            time += relativedelta(days=1)
        elif frequency == 'weekly':
            time += relativedelta(weeks=1)
        elif frequency == 'monthly':
            time += relativedelta(months=1)
        else:
            log.error("Frequency of {} is invalid!".format(frequency))
            return False
        return datetime.now().replace(tzinfo=time.tzinfo) >= time+relativedelta(minutes=-jitter_minutes)

    def snapshotInstanceWithFrequency(self, instance):
        """
        Snapshots the given instance's root volume if it needs to be snapshotted
        based on the configured frequency of daily, weekly, or monthly

        :type instance: dict
        :param instance: Contains the ec2.Instance object as well as config data
                         from the instances file
        
        :rtype: ec2.Snapshot
        :return: the snapshot that was taken, if any, or None
        """
        freq = instance.get("frequency")
        histsize = instance.get("historysize")
        snaps = instance.getRootVolumeSnapshots()
        desc = "Shutter automatically managed snapshot of {} ({})".format(instance.name, instance.instance.id)
        tags = {SETTING_TAG+"InstanceId": instance.instance.id}

        # If there are snaps then get the latest one, if not then just take one
        # as long as the history size isn't 0
        if len(snaps):
            latest = snaps[-1]
        elif histsize > 0:
            log.debug("Snapshotting " + instance.name)
            return instance.snapshot(desc, tags)

        bt = latest.meta.data['StartTime']

        if Shutter._timeWithinFrequency(bt, freq):
            log.debug("Snapshotting " + instance.name)
            return instance.snapshot(desc, tags)
        else:
            log.debug("Not snaphotting {} ({}) last snapshot too new with frequency {}".format(instance.name, instance.instance.id, freq))
            return None

    def _getInstanceById(self, id, region):
        """
        @@@ DEPRECATED @@@
        Gets an EC2 instance by its id

        :type id: string
        :param id: The id of the instance to get
        :type region: string
        :param region: The region to look for the instance in

        :rtype: ec2.Instance
        :return: The EC2 instance with the given id or None
        """
        self.initRegion(region)
        q = list(self.ec2[region].instances.filter(
            Filters=[
                {"Name": "instance-id",
                 "Values": [id]}
            ]
        ))
        return q[0] if len(q) else None

    def _getInstanceByName(self, name, region):
        """
        @@@ DEPRECATED @@@
        Gets an EC2 instance by its name tag

        :type name: string
        :param name: The name of the instance to get
        :type region: string
        :param region: The region to look for the instance in

        :rtype: ec2.Instance
        :return: The EC2 instance with the given name or None
        """
        self.initRegion(region)
        q = list(self.ec2[region].instances.filter(
            Filters=[
                {"Name": "tag:Name",
                 "Values": [name]}
            ]
        ))
        return q[0] if len(q) else None

    def copySnapshot(self, snap, source, dest, wait=True):
        """
        Copies a snapshot from one region to another

        :type snap: ec2.Snapshot
        :param snap: snapshot to copy
        :type source: str
        :param source: source region
        :type dest: str
        :param dest: destination region
        :type wait: bool
        :param wait: wait for the snapshot to finish or error before proceeding

        :rtype: ec2.Snapshot
        :return: the copy made, if any, else None
        """
        self.initRegion(dest)
        client = self.session.client('ec2', region_name=dest)

        while wait and snap.state != 'completed':
            snap.reload()
            if snap.state == 'error':
                log.error("Failed to complete snapshot, not copying")
                return None

        resp = client.copy_snapshot(SourceSnapshotId=snap.id, SourceRegion=source, Description=snap.description)

        if resp['ResponseMetadata']['HTTPStatusCode'] != 200:
            log.error("Copy failed")
            return None

        # get and return the actual snapshot object
        filt = [{"Name": "snapshot-id", "Values": [resp["SnapshotId"]]}]
        snapCopy = list(self.ec2[dest].snapshots.filter(Filters=filt))[0]
        if not snapCopy or snapCopy.state == 'error':
            log.error("Copy failed")
            return None

        # let's copy the tags from the other snapshot too
        snapCopy.create_tags(Tags=snap.tags)
        return snapCopy

    def getInstanceOffsiteBackupSnapshots(self, instance):
        """
        Get a list of offsite backup snapshots. Has to be done in this class
        because the Instance class does not have ec2 regions.

        :type instance: Instance
        :param instance: instance to get the offsite snapshots of

        :rtype: list(ec2.Snapshot)
        :return: a list of offsite snapshots managed by shutter
        """
        region = instance.get("OffsiteRegion")
        self.initRegion(region)
        q = list(self.ec2[region].snapshots.filter(
            Filters=[
                {"Name": "tag:{}InstanceId".format(SETTING_TAG),
                 "Values": [instance.instance.id]}
            ]
        ))
        q.sort(key=lambda i: i.meta.data["StartTime"])
        return q

    def makeOffsiteSnapshot(self, instance, snap):
        """
        Copy a snapshot to the region specified in the instance config

        :type instance: Instance
        :param instance: instance corresponding to snap
        :type snap: ec2.Snapshot
        :param snap: snapshot to copy

        :rtype: ec2.Snapshot
        :return: the new snapshot copy
        """
        log.debug("Copying snapshot of {} from {} to {}" + instance.name, instance.region, instance.get("offsiteregion"))
        return self.copySnapshot(snap, instance.region, instance.get("offsiteregion"))

    def makeOffsiteSnapshotWithFrequency(self, instance, snap):
        """
        Backup a snapshot offsite if it's time

        :type instance: Instance
        :param instance: instance corresponding to snap
        :type snap: ec2.Snapshot
        :param snap: snapshot to copy, if it's time

        :rtype: ec2.Snapshot
        :return: the new snapshot copy
        """
        freq = instance.get("offsitefrequency")
        histsize = instance.get("offsitehistorysize")
        offsite_snaps = self.getInstanceOffsiteBackupSnapshots(instance)

        if len(offsite_snaps):
            latest = offsite_snaps[-1]
        elif histsize > 0:
            return self.makeOffsiteSnapshot(instance, snap)

        bt = latest.meta.data['StartTime']

        if Shutter._timeWithinFrequency(bt, freq):
            return self.makeOffsiteSnapshot(instance, snap)
        else:
            log.debug("Not copying {} ({}) last offsite snapshot too new with frequency {}".format(instance.name, instance.instance.id, freq))
            return None

if __name__ == "__main__":
    import sys
    s = Shutter(sys.argv[1])
    s.run()
