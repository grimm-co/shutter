shutter
=======
Automatic snapshot manager for EC2. Gives you more features than the default AWS Lifecycle Manager  
Enable shutter by adding the region you wish to manage to the configuration and then tagging your instances with `Shutter-Enable` equal to `true` or `yes`  
Configuration options:

| Option                    | Description                                                    | Valid values or types                       |
| ------                    | -----------                                                    | ---------------------                       |
| AWSProfile                | The AWS credential profile to use                              | User defined via ~/aws/config & credentials |
| LogLevel                  | The Logger log level to log to shutter.log with                | debug, error, info, critical, warning. Case insensitive |
| Regions                   | The regions to search in for shutter managed snapshots         | yaml list (each item on a new line prefixed by -) |
| Default                   | Default values for instance configurations.                    | See table below                             |

Defaults:

| Option                    | Description                                                    | Valid values or types                       |
| ------                    | -----------                                                    | ---------------------                       |
| Frequency                 | The default frequency at which instances are snapshotted       | daily, weekly, monthly                      |
| HistorySize               | The number of snapshots to keep before deleting the oldest one | integer                                     |
| DeleteOldSnapshots        | Debug option for whether to delete old snapshots or not        | boolean                                     |
| RootDevice                | The default root device name. Usually /dev/sda1                | string                                      |
| OffsiteBackup             | Set to True to enable offsite backups                          | boolean                                     |
| OffsiteRegion             | The region to copy offsite backups to                          | string                                      |
| OffsiteFrequency          | The frequency at which instances are backed up offsite         | string                                      |
| OffsiteHistorySize        | The size of the offsite backup history                         | daily, weekly, monthly                      |
| OffsiteEncrypt            | Set to True to enable encryption of all offsite snapshots      | boolean                                     |
| OffsiteKmsId              | The IAM KMS key ID to encrypt with. No aliases allowed         | daily, weekly, monthly                      |



Defaults can be overridden with AWS tags. Ex. to override DefaultFrequency just make a tag on the instance Shutter-DefaultFrequency and set it to the value you want. Shutter will pick it up!

These configuration options are set in [config.yml](config.yml)

You can use the IAM policy in [shutter-policy.json](shutter-policy.json) to only allow access to the specific resources shutter needs to operate. Also, you need to grant the shutter user/role access to use the encryption keys you specify, if you use offsite snapshot encryption.  

Developed with love by Jaime Geiger (@wumb0)
