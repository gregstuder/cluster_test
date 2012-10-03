#!/bin/bash

PIDFILE="../process_manager.pid"
OUTFILE="../process_manager.out"

if [ -e "$PIDFILE" ]; then

RUNNING=`cat $PIDFILE`

if [ -n "$RUNNING" ]; then
    kill $RUNNING
    sleep 1
    
    if [ -n "`ps aux | grep python | grep process_manager.py | grep $RUNNING`" ]; then
        kill -9 $RUNNING
        sleep 1
    fi
    
    rm $OUTFILE
fi

fi

nohup python -u ./process_manager.py $1>$OUTFILE 2>&1 </dev/null &
echo $! > $PIDFILE