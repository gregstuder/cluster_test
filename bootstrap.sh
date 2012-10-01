#!/bin/bash

BASEDIR=$(dirname $0)

# GLOBAL
EC2_PRIVATE_KEY="`pwd`/`ls $1/pk-*.pem`"
EC2_CERT="`pwd`/`ls $1/cert-*.pem`"
SSH_KEYFILE="`pwd`/`ls $1/id_*.private`"
EC2_KEY_NAME="`echo $SSH_KEYFILE | sed 's/.*id_\(.*\)\.private/\1/'`"

SETUP_LOCK_FILE="./.wait_for_used"

echo
echo "Starting script with data in $1 : "
echo " EC2 PRIVATE KEY : $EC2_PRIVATE_KEY"
echo " EC2 CERT : $EC2_CERT"
echo " EC2 INSTANCE KEY : $SSH_KEYFILE"
echo " EC2 INSTANCE KEY NAME : $EC2_KEY_NAME"
echo

shift

command -v ec2-version >/dev/null 2>&1 || { echo >&2 "Script requires ec2 command line tools but they aren't installed.  Aborting."; exit 1; }
command -v ssh >/dev/null 2>&1 || { echo >&2 "Script requires ssh but it's not installed.  Aborting."; exit 1; }
command -v scp >/dev/null 2>&1 || { echo >&2 "Script requires scp but it's not installed.  Aborting."; exit 1; }

function bootstrap {

# Wait for all instances to be sorted before setting up
while [ -e $SETUP_LOCK_FILE ]; do sleep 1; done

local NAME="$1"
local FULL_TYPE="$2"
local TYPE="$3"
local PROPS="$4"

local INSTANCE_ID="$5"
local EC2_USER=""

if [ "$INSTANCE_ID" = "" ]; then

echo "Creating instance with name $NAME, type $TYPE, and properties $PROPS..."

if [ "$TYPE" = "chef-server" ]; then

EC2_USER="ubuntu"
INSTANCE_ID=$( ec2-run-instances -C "$EC2_CERT" -K "$EC2_PRIVATE_KEY" ami-3c994355 -k $EC2_KEY_NAME -t "m1.medium" -z "us-east-1a" | egrep ^INSTANCE | cut -f2 )

elif [ "$TYPE" = "dummy" ]; then

EC2_USER="dummy-user"
INSTANCE_ID="i-0123456f"

else

echo
echo "Unknown type $TYPE????"
echo

return

fi

echo 
echo "Created instance with id of : $INSTANCE_ID"
echo
echo "Run to terminate : " 
echo "ec2-terminate-instances -C "$EC2_CERT" -K "$EC2_PRIVATE_KEY" $INSTANCE_ID"
echo 

mkdir -p ./instances/$INSTANCE_ID

ln -s "`pwd`/instances/$INSTANCE_ID" "`pwd`/used_instances/$INSTANCE_ID"

if [ "$NAME" = "" ]; then 
    echo "No name specified for instance of type $TYPE"
else
    rm -R ./named_instances/$NAME
    ln -s "`pwd`/instances/$INSTANCE_ID" "`pwd`/named_instances/$NAME"
fi

else

EC2_USER="`cat ./instances/$INSTANCE_ID/ec2_user`"

echo
echo "Instance $INSTANCE_ID is already created..."
echo

fi

mkdir -p ./instances/$INSTANCE_ID

echo $INSTANCE_ID > ./instances/$INSTANCE_ID/instance
echo $FULL_TYPE > ./instances/$INSTANCE_ID/type
echo $PROPS > ./instances/$INSTANCE_ID/props
echo $EC2_PRIVATE_KEY > ./instances/$INSTANCE_ID/ec2_private_key
echo $EC2_CERT > ./instances/$INSTANCE_ID/ec2_cert
echo $SSH_KEYFILE > ./instances/$INSTANCE_ID/keyfile
echo $EC2_USER > ./instances/$INSTANCE_ID/ec2_user

if [ "$TYPE" = "dummy" ]; then 
    
    echo "Dummy instance, not starting host..."
    
    local HOST="ec2-000-00-00-000"
    local INTERNAL="ip-000-00-00-000"
    
else

    # wait for the instance to be fully operational
    echo 
    echo -n "Waiting for instance to start running..."
    #echo ec2-describe-instances -C "$EC2_CERT" -K "$EC2_PRIVATE_KEY" "$INSTANCE_ID" 
    while local HOST=$(ec2-describe-instances -C "$EC2_CERT" -K "$EC2_PRIVATE_KEY" "$INSTANCE_ID" | egrep ^INSTANCE | cut -f4) && test -z $HOST; do echo -n .; sleep 1; done

    local INTERNAL=$(ec2-describe-instances -C "$EC2_CERT" -K "$EC2_PRIVATE_KEY" "$INSTANCE_ID" | egrep ^INSTANCE | cut -f5)

fi

echo $HOST > ./instances/$INSTANCE_ID/host
echo $INTERNAL > ./instances/$INSTANCE_ID/internal

echo 
echo "Running as $HOST (internally $INTERNAL)"
echo

if [ "$TYPE" = "dummy" ]; then 
    
    echo "Dummy instance, not connecting to host..."
    
else

    if [ "`echo $PROPS | grep '.nossh'`" = "" ]; then 
        
        echo 
        echo -n "Verifying ssh connection to box..."
        #echo ssh -o StrictHostKeyChecking=no -o IdentitiesOnly=yes -q -i "$SSH_KEYFILE" $EC2_USER@$HOST
        while ! ssh -o StrictHostKeyChecking=no -o IdentitiesOnly=yes -q -i "$SSH_KEYFILE" $EC2_USER@$HOST true; do echo -n .; sleep 1; done
        echo 
        
    else
    
        echo "No SSH connection required in properties, not connecting..."
    
    fi

fi

cat > ./instances/$INSTANCE_ID/connect <<EOF
#!/bin/bash
CURR_HOST=\$(ec2-describe-instances -C "$EC2_CERT" -K "$EC2_PRIVATE_KEY" "$INSTANCE_ID" | egrep ^INSTANCE | cut -f4)
echo "Connecting to \$CURR_HOST..."
ssh -o StrictHostKeyChecking=no -o IdentitiesOnly=yes -i "$SSH_KEYFILE" $EC2_USER@\$CURR_HOST
EOF
chmod +x ./instances/$INSTANCE_ID/connect

echo -n "Host $HOST is now up and connectable."
echo 
echo "Run to connect : "
echo "ssh -o StrictHostKeyChecking=no -o IdentitiesOnly=yes -i "$SSH_KEYFILE" $EC2_USER@$HOST"
echo 

# Wait for all hosts to be sorted before doing script
if [ "$NAME" = "" ]; then 
    echo "No name to add to host file for instance $INSTANCE_ID..."
else
    echo $NAME $HOST >> ./hosts
fi
while [ -e ./hosts_temp ]; do sleep 1; done

if [ "`echo $PROPS | grep '.empty'`" = "" ]; then 
    
    scp -o StrictHostKeyChecking=no -o IdentitiesOnly=yes -i "$SSH_KEYFILE" -r "./remote_data/$TYPE" $EC2_USER@$HOST:~/remote_data/
    scp -o StrictHostKeyChecking=no -o IdentitiesOnly=yes -i "$SSH_KEYFILE" ./hosts $EC2_USER@$HOST:~/hosts
    ssh -o StrictHostKeyChecking=no -o IdentitiesOnly=yes -t -i "$SSH_KEYFILE" -t $EC2_USER@$HOST "sudo su -c 'chmod +x ./remote_data/bs.sh'; sudo su -c './remote_data/bs.sh $TYPE $EC2_USER'"
    
else
    
    echo "Empty instance, not setting up host..."
    
fi

echo 
echo "Run to connect to instance $INSTANCE_ID : "
echo "ssh -o StrictHostKeyChecking=no -o IdentitiesOnly=yes -i \"$SSH_KEYFILE\" -A $EC2_USER@$HOST"
echo 

}

function cleanup {
    
    echo
    echo "Terminating all instances..."
    echo    
    for OLD_INSTANCE_ID in `ls ./instances`; do
        
        echo "Terminating instance $OLD_INSTANCE_ID..."
        ec2-terminate-instances -C "$EC2_CERT" -K "$EC2_PRIVATE_KEY" $OLD_INSTANCE_ID
        fusermount -u ./instances/$OLD_INSTANCE_ID/home
    	rm -Rf ./instances/$OLD_INSTANCE_ID        

    done
    echo

}

mkdir -p ./instances

rm -Rf ./used_instances/*
rm -Rf ./named_instances/*
mkdir -p ./used_instances
mkdir -p ./named_instances

if [ "$1" = "new" ]; then
    cleanup
    shift
fi

# Don't setup hosts until we've figured out all the details
rm $SETUP_LOCK_FILE
touch $SETUP_LOCK_FILE

# Clear the hosts file
rm ./hosts
rm ./hosts_temp
touch ./hosts
touch ./hosts_temp

PIDS=""
while [ "$1" ]; do
    
    NAME="`echo $1 | grep ':' | sed 's/^\(.*\):.*$/\1/'`"
    FULL_TYPE="`echo $1 | sed 's/^.*://'`"
    TYPE="`echo $FULL_TYPE | sed 's/^\(.*\)\..*/\1/'`"
    PROPS="`echo $FULL_TYPE | grep '.' | sed 's/^.*\././'`"
    FULL_TYPE="$TYPE$PROPS"
    
    RUNNING=$(find ./instances -name type -print -exec cat '{}' \; | sed -e 'N;s/\n/ /' | grep small32 | sed 's/\.\/instances\/\(.*\)\/type.*/\1/')
    USED=$(find -L ./used_instances -name type -print -exec cat '{}' \; | sed -e 'N;s/\n/ /' | grep small32 | sed 's/\.\/used_instances\/\(.*\)\/type.*/\1/')
    #RUNNING=$( grep -R -l "$FULL_TYPE" ./instances | sed 's/\//\t/g' | cut -f3 )
    #USED=$( grep -R -l "$FULL_TYPE" ./used_instances | sed 's/\//\t/g' | cut -f3 )

    echo
    echo "Running of type $FULL_TYPE:"
    echo $RUNNING
    echo "Used of type $FULL_TYPE:"
    echo $USED   

    for R in $RUNNING; do
        
        NOTUSED=$R
        for U in $USED; do 
            if [ "$R" = "$U" ]; then
                NOTUSED=""
                break
            fi
        done

        if [ "$NOTUSED" != "" ]; then
            break
        fi  

    done
    
    if [ "$NOTUSED" = "" ]; then
        
        # Create an empty named dir for the hosts file, for now
        if [ "$NAME" = "" ]; then 
            NAME="???"
            echo "No name specified for new instance of type $TYPE"
        else
            mkdir -p ./named_instances/$NAME
        fi
    
        echo "Need to create new server for $FULL_TYPE..."        
        echo
        
        bootstrap $NAME $FULL_TYPE $TYPE $PROPS &
        PIDS="$PIDS $!"
    else
        echo "Reusing $NOTUSED for $FULL_TYPE..."
        ln -s "`pwd`/instances/$NOTUSED" "`pwd`/used_instances/$NOTUSED"
        
        if [ "$NAME" = "" ]; then 
            echo "No name specified for active instance of type $TYPE"
        else
            ln -s "`pwd`/instances/$NOTUSED" "`pwd`/named_instances/$NAME"
        fi
        
        CURR_HOST=$(ec2-describe-instances -C "$EC2_CERT" -K "$EC2_PRIVATE_KEY" "$NOTUSED" | egrep ^INSTANCE | cut -f4)
        echo $CURR_HOST > ./instances/$NOTUSED/host
        
        echo "Current host is $CURR_HOST..."
        echo
        
        bootstrap $NAME $FULL_TYPE $TYPE $PROPS $NOTUSED &
        PIDS="$PIDS $!"
    fi
    
    shift

done

NAMED=$( ls ./named_instances )

for N in $NAMED; do
    
    echo $N $(cat ./named_instances/$N/host) >> ./hosts_temp
    
done

echo 
echo Named hosts are :
cat ./hosts_temp
echo

echo
echo "######"
echo "Now setting up all hosts at once..."
echo "######"
echo

# Unlock all setup at once
rm $SETUP_LOCK_FILE

for PID in $PIDS; do
    while ISUP=$( ps -A | grep $PID ) && test -n "$ISUP"; do
        
        if [ -e ./hosts_temp ]; then
        if [ "`wc -l ./hosts_temp | awk '{print $1}'`" = "`wc -l ./hosts | awk '{print $1}'`" ]; then
            echo
            echo "*****"
            echo "All named hosts have been initialized..."
            echo "*****"
            echo
            rm ./hosts_temp
        fi
        fi
        
        sleep 1; 
    done
done

rm ./hosts_temp

USED=$( ls ./used_instances )
NAMED=$( ls ./named_instances )

echo "// Servers available " > ./hosts.js
echo "MongoRunner.servers = []" >> ./hosts.js

for U in $USED; do
    
    TYPE="`cat ./instances/$U/type`"
    HOST="`cat ./instances/$U/host`"
    SSH_KEYFILE="`cat ./instances/$U/keyfile`"
    EC2_USER="`cat ./instances/$U/ec2_user`"
    
    fusermount -u ./instances/$U/home
    rm -R ./instances/$U/home
    mkdir -p ./instances/$U/home
    
    # HAVE TO DO THIS, STUPID SSHFS
    ln -s "$SSH_KEYFILE" ./instances/$U/linked_keyfile
    
    echo "Mounting ssh dir for $HOST / $U..."
    SSH_COMMAND="ssh -i ./instances/$U/linked_keyfile"
    #echo $SSH_COMMAND
    #echo sshfs -o ssh_command=\"$SSH_COMMAND\" $EC2_USER@$HOST:. ./instances/$U/home
    sshfs -o ssh_command="$SSH_COMMAND" $EC2_USER@$HOST:. ./instances/$U/home
    echo "Mounted."
        
    echo "servers.push({ host : \"$HOST\", ssh : { sshKey : \"$SSH_KEYFILE\", sshUser : \"$EC2_USER\" }, ec2 : { type : \"$TYPE\", instance : \"$U\" } })" >> ./hosts.js

done



echo
echo "Javascript: " 
echo
cat ./hosts.js






