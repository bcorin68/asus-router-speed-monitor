# ASUS Router Speed Monitor

A lightweight router-side Internet speed-test collector and web dashboard.

The Linux host does NOT run the Internet speed test itself. It connects to an
ASUS router over SSH and requests the router's built-in Ookla speed test.

## Requirements

- Linux with systemd
- Python 3
- OpenSSH client
- ASUS router with SSH enabled
- Router shell containing the required `ookla` command
- SSH key authentication from the Linux host to the router

No Python packages or pip installation are required.

## Installation

Extract the archive and run:

    bash install.sh

The installer asks for:

- Router IP/address
- Router SSH username
- SSH private-key path
- known_hosts path
- Dashboard listen address
- Dashboard port
- History retention period

The SSH private key is NOT copied by the application.

## Running a test manually

    sudo systemctl start router-speedtest.service

## Check test results/errors

    sudo journalctl -u router-speedtest.service -n 30 --no-pager

## Dashboard

The default port is TCP/8090.

## Schedule

Tests run at:

- 00:07
- 06:07
- 12:07
- 18:07

The installer asks which timezone should be used for the schedule.

Missed tests are not automatically run later.

WARNING: High-speed Ookla tests can transfer many gigabytes of data per test.
