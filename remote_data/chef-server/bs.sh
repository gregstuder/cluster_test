#!/bin/bash

# Install chef repo
echo "deb http://apt.opscode.com/ `lsb_release -cs`-0.10 main"\
 | tee /etc/apt/sources.list.d/opscode.list
 
mkdir -p /etc/apt/trusted.gpg.d
gpg --keyserver keys.gnupg.net --recv-keys 83EF826A
gpg --export packages@opscode.com | tee /etc/apt/trusted.gpg.d/opscode-keyring.gpg > /dev/null

apt-get update
apt-get install opscode-keyring # permanent upgradeable keyring

# Install chef
echo "chef chef/chef_server_url string http://`curl http://instance-data/latest/meta-data/hostname`:4000"\
 | debconf-set-selections
echo "chef-solr chef-solr/amqp_password password 12345"\
 | debconf-set-selections
echo "chef-server-webui chef-server-webui/admin_password password 12345"\
 | debconf-set-selections
apt-get install chef chef-server -y

# Because of a bug, the webui won't start up the first time around for some reason

echo "Restarting chef webui because of a bug..."

/etc/init.d/chef-server-webui stop
/usr/sbin/chef-server-webui -p 4002 &
sleep 10
killall chef-server-web
sleep 5
/etc/init.d/chef-server-webui start

# Install knife-ec2

apt-get install -y ruby1.8-dev ruby1.8 ri1.8 rdoc1.8 irb1.8
apt-get install -y libreadline-ruby1.8 libruby1.8 libopenssl-ruby
apt-get install -y libxslt-dev libxml2-dev

gem install knife-ec2 --no-rdoc --no-ri

mkdir -p .chef; touch .chef/knife.rb
chown -R ubuntu:ubuntu .chef

# GLOBAL
EC2_DIR="remote_data/aws_test"
EC2_API_KEY="`cat $EC2_DIR/*.apiuser`"
EC2_API_PRIVATE_KEY="`cat $EC2_DIR/*.apikey`"
SSH_KEYFILE="`pwd`/`ls $EC2_DIR/id_*.private`"
EC2_KEY_NAME="`echo $SSH_KEYFILE | sed 's/.*id_\(.*\)\.private/\1/'`"

echo
echo "AWS data in $EC2_DIR : "
echo " EC2 API KEY : $EC2_API_KEY"
echo " EC2 INSTANCE KEY : $SSH_KEYFILE"
echo " EC2 INSTANCE KEY NAME : $EC2_KEY_NAME"
echo

DEFAULT_USER="ubuntu"
echo "User is : $USER / $DEFAULT_USER"

usermod -a -G chef $DEFAULT_USER
chmod g+r /etc/chef/validation.pem
chmod g+r /etc/chef/webui.pem

rm -f ./.chef/knife.rb
su $DEFAULT_USER -c "knife configure --defaults -i -r \"\""

#echo "chef_server_url          'http://`curl http://instance-data/latest/meta-data/hostname`:4000'" >> .chef/knife.rb
echo "knife[:aws_ssh_key_id] = \"$EC2_KEY_NAME\"" >> ./.chef/knife.rb
echo "knife[:aws_access_key_id] = \"$EC2_API_KEY\"" >> ./.chef/knife.rb
echo "knife[:aws_secret_access_key] = \"$EC2_API_PRIVATE_KEY\"" >> ./.chef/knife.rb


echo "if [ -f ~/.bashrc ]; then . ~/.bashrc; fi" >> ./.bash_profile
echo "ssh-add $SSH_KEYFILE" >> ./.bashrc

chown $DEFAULT_USER:$DEFAULT_USER ./.bash_profile
chown $DEFAULT_USER:$DEFAULT_USER ./.bashrc

# Install git for cookbooks

apt-get install -y git

#su $DEFAULT_USER -c "git clone git://github.com/opscode/chef-repo.git"

echo "cookbook_path '/home/ubuntu/remote_data/chef-repo/cookbooks'" >> .chef/knife.rb

for recipe in "getting-started" "chef-client" "mongodb"; do
	su $DEFAULT_USER -c "knife cookbook site install $recipe"
done

# Upload all installed cookbooks
su $DEFAULT_USER -c "knife cookbook upload -a"

# Install all roles
for role in `ls ./remote_data/chef-repo/roles/*.rb`; do
        su $DEFAULT_USER -c "knife role from file $role"
done

echo "To login to chef server: "
echo "http://`curl http://instance-data/latest/meta-data/public_hostname`:4040"

echo "To start an example node with chef-client daemon:"
echo " knife ec2 server create -I ami-b89842d1 -x ubuntu -Z us-east-1a"
echo " knife node run_list add NODENAME 'recipe[chef-client]'"
echo " knife ssh name:NODENAME -x ubuntu 'sudo chef-client'"

exit

