#!/bin/bash

#MONGO_USER="ec2-user"
#TYPE="large"

#if echo "$TYPE" | grep "32"; then
#    ARCH="i686"
#else
#    ARCH="x86_64"
#fi

#echo -e "[10gen]\nname=10gen Repository\nbaseurl=http://downloads-distro.mongodb.org/repo/redhat/os/$ARCH\ngpgcheck=0\n" > /etc/yum.repos.d/10gen.repo

#yum install -y mongo-10gen-server
#yum install -y gcc
#yum install -y python-devel
#yum install -y python-setuptools
#yum install -y python-setuptools-devel
#yum install -y mdadm
#yum install -y xfsprogs
#yum install -y telnet
#yum install -y munin-node
#echo -e "\nallow .*\n" >> /etc/munin/munin-node.conf
#/etc/init.d/munin-node stop
#/etc/init.d/munin-node start

#echo 300 > /proc/sys/net/ipv4/tcp_keepalive_time
#echo -e "*  hard    nproc   20000\n*    soft    nproc   20000\n*    hard    nofile  20000\n*    soft    nofile  20000\n" > /etc/security/limits.conf

#easy_install -U setuptools
#easy_install pip
#pip install pymongo

#echo $TYPE

#if [ "$TYPE" = "shard" ]; then

#mdadm --create --verbose /dev/md0 --level=10 --chunk=256 --raid-devices=6 /dev/sdb /dev/sdc /dev/sdd /dev/sde /dev/sdf /dev/sdg
#mkfs.xfs -f /dev/md0
#mkdir -p /data/raid
#mkdir -p /data/standard
#echo "/dev/md0	/data/raid	auto	defaults,nobootwait,noatime	0	0" >> /etc/fstab
#mount /dev/md0 /data/raid

#mdadm --detail /dev/md0
#cat /proc/mdstat

#killall -s 9 mongod

#rm -Rf /data/raid/*
#rm -Rf /data/standard/*
#ln -s /data/raid /data/db
#chown -R $MONGO_USER:$MONGO_USER /data/*

#fi
#if [ "$TYPE" = "legacyShard" ]; then

#/sbin/mdadm --create --verbose /dev/md0 --level=10 --chunk=256 --raid-devices=6 /dev/sdb /dev/sdc /dev/sdd /dev/sde /dev/sdf /dev/sdg
#/sbin/mkfs.xfs -f /dev/md0
#mkdir -p /data/raid
#mkdir -p /data/standard
#echo "/dev/md0	/data/raid	auto	defaults,nobootwait,noatime	0	0" >> /etc/fstab
#mount /dev/md0 /data/raid

#/sbin/mdadm --detail /dev/md0
#cat /proc/mdstat

#killall -s 9 mongod

#rm -Rf /data/raid/*
#rm -Rf /data/standard/*
#ln -s /data/raid /data/db
#chown -R $MONGO_USER:$MONGO_USER /data/*

#fi

#ulimit -n 20000
#ulimit -u 20000
#ulimit -a

#mkdir -p /var/log

#echo "Starting iostat..."
#su -c "nohup iostat -xmt 2 > /var/log/iostat.log 2>&1 &" $MONGO_USER

#echo "Started..."

#sleep 3

#tail ./logs/iostat.log
