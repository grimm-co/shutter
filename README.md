shutter
=======
Automatic snapshot manager for EC2.

Configuration options:
| Option             | Description                                                    | Valid values or types                  |
| ------             | -----------                                                    | ---------------------                  |
| DefaultAWSRegion   | The default region to look for the instance in                 | See [here](http://docs.aws.amazon.com/general/latest/gr/rande.html) |
| AWSProfile         | The AWS credential profile to use                              | User defined via ~/aws/config & credentials |
| DefaultFrequency   | The default frequency at which instances are snapshotted       | daily, weekly, monthly                 |
| DefaultHistorySize | The number of snapshots to keep before deleting the oldest one | integer                                |
| DefaultTime        | The time of day to snapshot VMs                                | time in 24-hour clock format           |
| DefaultDayOfWeek   | The day of the week to take weekly snapshots                   | 0-6 (Sunday-Saturday)                  |
| DefaultDayOfMonth  | The day of the month to take monthly snapshots                 | 0-28 for simplicity                    |
| DefaultDeleteOldSnapshots | Debug option for whether to delete old snapshots or not        | boolean                                |

These configuration options are set in [config.yml](config.yml)

Hosts are defined in instances are set in instances.yml. Check out the example [instances.yml](instances.yml) for details.
