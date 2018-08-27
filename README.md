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
| DefaultFrequency          | The default frequency at which instances are snapshotted       | daily, weekly, monthly                      |
| DefaultHistorySize        | The number of snapshots to keep before deleting the oldest one | integer                                     |
| DefaultDeleteOldSnapshots | Debug option for whether to delete old snapshots or not        | boolean                                     |
| DefaultRootDevice         | The default root device name. Usually /dev/sda1                | string                                      |

Defaults can be overridden with AWS tags. Ex. to override DefaultFrequency just make a tag on the instance Shutter-DefaultFrequency and set it to the value you want. Shutter will pick it up!

These configuration options are set in [config.yml](config.yml)

Developed with love by Jaime Geiger (@wumb0)
