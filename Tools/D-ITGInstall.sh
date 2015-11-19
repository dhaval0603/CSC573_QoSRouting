apt-get install d-itg
cp D-ITG/D-ITG-2.8.1-r1023-src.zip ~
cd ~
unzip D-ITG-2.8.1-r1023-src.zip
cd D-ITG-2.8.1-r1023/src
make
cd ~
rm -rf D-ITG-2.8.1-r1023-src.zip
export $PATH=$PATH:~/D-ITG-2.8.1-r1023/bin
