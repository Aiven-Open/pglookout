#!/bin/bash
# Example failover script to be run as a pglookout failover command
# Usage ./failover.sh
# You can for example edit the IP below to match your IP aliasing config

# Set an IP alias that moves to the new master node
sudo ifconfig eth0:0 1.2.3.4 netmask 255.255.255.0

# Promote the new master node

pg_ctl promote -D /var/lib/pgsql/data

# Implement STONITH for your use case here
