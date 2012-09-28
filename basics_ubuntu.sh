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
echo "chef chef/chef_server_url string none"\
 | debconf-set-selections && sudo apt-get install chef -y

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