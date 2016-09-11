shutter
=======
Automatic snapshot manager for EC2.

Configuration options:
| Option                    | Description                                                    | Valid values or types                       |
| ------                    | -----------                                                    | ---------------------                       |
| DefaultAWSRegion          | The default region to look for the instance in                 | See [here](http://docs.aws.amazon.com/general/latest/gr/rande.html) |
| AWSProfile                | The AWS credential profile to use                              | User defined via ~/aws/config & credentials |
| DefaultFrequency          | The default frequency at which instances are snapshotted       | daily, weekly, monthly                      |
| DefaultHistorySize        | The number of snapshots to keep before deleting the oldest one | integer                                     |
| DefaultDeleteOldSnapshots | Debug option for whether to delete old snapshots or not        | boolean                                     |
| LogLevel                  | The Logger log level to log to shutter.log with                | debug, error, info, critical, warning. Case insensitive |

These configuration options are set in [config.yml](config.yml)

Hosts are defined in instances are set in instances.yml. Check out the example [instances.yml](instances.yml) for details.
