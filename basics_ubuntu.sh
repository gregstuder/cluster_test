#/bin/bash

# Install chef repo
echo "deb http://apt.opscode.com/ `lsb_release -cs`-0.10 main"\
 | tee /etc/apt/sources.list.d/opscode.list
 
mkdir -p /etc/apt/trusted.gpg.d
gpg --keyserver keys.gnupg.net --recv-keys 83EF826A
gpg --export packages@opscode.com | tee /etc/apt/trusted.gpg.d/opscode-keyring.gpg > /dev/null

apt-get update
apt-get install opscode-keyring # permanent upgradeable keyring

# Install chef
echo "chef chef/chef_server_url string http://`curl http://instance-data/latest/meta-data/public-hostname`:4000"\
 | debconf-set-selections && sudo apt-get install chef chef-server -y


#
#
#
#



# Install ruby and gem dependencies
apt-get install -y ruby ruby-dev libopenssl-ruby rdoc ri irb build-essential wget ssl-cert curl

mkdir ./ruby-tmp
cd ./ruby-tmp

curl -O http://production.cf.rubygems.org/rubygems/rubygems-1.8.10.tgz
tar zxf rubygems-1.8.10.tgz
cd rubygems-1.8.10
ruby setup.rb --no-format-executable

cd ../..
rm -R ./ruby-tmp

gem install chef --no-ri --no-rdoc

# Setup and run chef-solo
mkdir -p /etc/chef

echo "\
file_cache_path \"/tmp/chef-solo\"
cookbook_path \"/tmp/chef-solo/cookbooks\"" | tee /etc/chef/solo.rb

read -d '' CONFIG <<"EOF"
{
  "chef_server": {
    "server_url": "http://localhost:4000"
  },
  "run_list": [ "recipe[chef-server::rubygems-install]" ]
}
EOF
echo "$CONFIG" | tee chef.json

chef-solo -c /etc/chef/solo.rb -j ~/chef.json -r http://s3.amazonaws.com/chef-solo/bootstrap-latest.tar.gz

# Setup knife

#mkdir -p ~/.chef
#cp /etc/chef/validation.pem /etc/chef/webui.pem ~/.chef
#chown -R $USER ~/.chef

# Need to hit enter...
knife configure -i

# Install knife-ec2

apt-get update
apt-get install -y ruby1.8-dev ruby1.8 ri1.8 rdoc1.8 irb1.8
apt-get install -y libreadline-ruby1.8 libruby1.8 libopenssl-ruby
apt-get install -y libxslt-dev libxml2-dev

gem install knife-ec2 --no-rdoc --no-ri