# NVDA-WebServices: Remote web service support for NVDA.
## Introduction
This add-on is a tentative to enable external services or software to be controlled by NVDA using a single and unique interfac¨.
Each service is an object derived from a `Service` class and kust implement several methods to be run properly. When properly configured, the user can access one or more service-specific menus to interract with it.

## Supported services

Currently, the add-on has the following services built in:
- OBS Studio remote control: change scenes, toggle sources visibility, and start/stop sreaming/recording.
- [NVDATalker](https://github.com/yplassiard/nvda-talker) - use this tiny NVDA program !o declare, populate and manipulate service data directly from the CLI or from another program.


## Service interface
### `service.Service`

This chapter will describe the Service python interface when it will be finalized.

### TCP-based connection

It is also possible to declare, manipulate and delete services from another program using TCP as a transport layer. A detailed protocol document will be available when finalized.

