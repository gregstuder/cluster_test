#!/bin/bash

MONGO_USER="ec2-user"
TYPE="large"

if echo "$TYPE" | grep "32"; then
    ARCH="i686"
else
    ARCH="x86_64"
fi

echo -e "[10gen]\nname=10gen Repository\nbaseurl=http://downloads-distro.mongodb.org/repo/redhat/os/$ARCH\ngpgcheck=0\n" > /etc/yum.repos.d/10gen.repo

yum install -y mongo-10gen-server
yum install -y gcc
yum install -y python-devel
yum install -y python-setuptools
yum install -y python-setuptools-devel
yum install -y mdadm
yum install -y xfsprogs
yum install -y telnet
yum install -y munin-node
echo -e "\nallow .*\n" >> /etc/munin/munin-node.conf
/etc/init.d/munin-node stop
/etc/init.d/munin-node start

echo 300 > /proc/sys/net/ipv4/tcp_keepalive_time
echo -e "*  hard    nproc   20000\n*    soft    nproc   20000\n*    hard    nofile  20000\n*    soft    nofile  20000\n" > /etc/security/limits.conf

easy_install -U setuptools
easy_install pip
pip install pymongo

echo $TYPE

if [ "$TYPE" = "shard" ]; then

mdadm --create --verbose /dev/md0 --level=10 --chunk=256 --raid-devices=6 /dev/sdb /dev/sdc /dev/sdd /dev/sde /dev/sdf /dev/sdg
mkfs.xfs -f /dev/md0
mkdir -p /data/raid
mkdir -p /data/standard
echo "/dev/md0	/data/raid	auto	defaults,nobootwait,noatime	0	0" >> /etc/fstab
mount /dev/md0 /data/raid

mdadm --detail /dev/md0
cat /proc/mdstat

killall -s 9 mongod

rm -Rf /data/raid/*
rm -Rf /data/standard/*
ln -s /data/raid /data/db
chown -R $MONGO_USER:$MONGO_USER /data/*

fi
if [ "$TYPE" = "legacyShard" ]; then

/sbin/mdadm --create --verbose /dev/md0 --level=10 --chunk=256 --raid-devices=6 /dev/sdb /dev/sdc /dev/sdd /dev/sde /dev/sdf /dev/sdg
/sbin/mkfs.xfs -f /dev/md0
mkdir -p /data/raid
mkdir -p /data/standard
echo "/dev/md0	/data/raid	auto	defaults,nobootwait,noatime	0	0" >> /etc/fstab
mount /dev/md0 /data/raid

/sbin/mdadm --detail /dev/md0
cat /proc/mdstat

killall -s 9 mongod

rm -Rf /data/raid/*
rm -Rf /data/standard/*
ln -s /data/raid /data/db
chown -R $MONGO_USER:$MONGO_USER /data/*

fi

function getVersion {

VERSION=$1

if [ ! -e mongodb-linux-$ARCH-$VERSION.tgz ]; then

wget http://fastdl.mongodb.org/linux/mongodb-linux-$ARCH-$VERSION.tgz

if [ "$VERSION" = "latest" ]; then
    mkdir -p mongodb-linux-$ARCH-$VERSION
    tar -xzf mongodb-linux-$ARCH-$VERSION.tgz -C mongodb-linux-$ARCH-$VERSION
    ln -s `pwd`/mongodb-linux-$ARCH-$VERSION/`ls mongodb-linux-$ARCH-$VERSION/`/bin mongodb-linux-$ARCH-$VERSION/bin
else
    tar -xzf mongodb-linux-$ARCH-$VERSION.tgz
fi

chown -R $MONGO_USER:$MONGO_USER mongodb-linux-$ARCH-$VERSION/

fi

ln -s `pwd`/mongodb-linux-$ARCH-$VERSION/bin/mongod /usr/local/bin/mongod-r$VERSION
ln -s `pwd`/mongodb-linux-$ARCH-$VERSION/bin/mongos /usr/local/bin/mongos-r$VERSION
ln -s `pwd`/mongodb-linux-$ARCH-$VERSION/bin/mongo /usr/local/bin/mongo-r$VERSION

}

#getVersion 2.0.2
#getVersion 2.0.3-rc1
getVersion latest

ulimit -n 20000
ulimit -u 20000
ulimit -a

mkdir -p ./logs
chown -R $MONGO_USER:$MONGO_USER ./logs
if [ "$MONGO_USER" = "root" ]; then
    echo "Starting root iostat..."
    nohup iostat -xmt 2 > ./logs/iostat.log 2>&1 &
else
    echo "Starting iostat..."
    su -c "nohup iostat -xmt 2 > ./logs/iostat.log 2>&1 &" $MONGO_USER
fi
echo "Started..."

sleep 3

tail ./logs/iostat.log
