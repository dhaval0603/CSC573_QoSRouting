"""Custom topology example

Two directly connected switches plus a host for each switch:

   host --- switch --- switch --- host

Adding the 'topos' dict with a key/value pair to generate our newly defined
topology enables one to pass in '--topo=mytopo' from the command line.
"""

from mininet.topo import Topo
from mininet.link import TCLink

class MyTopo( Topo ):
    "Simple topology example."

    def __init__( self ):
        "Create custom topo."

        # Initialize topology
        Topo.__init__( self )

        # Add hosts and switches
        h1Host = self.addHost( 'h1' )
        h2Host = self.addHost( 'h2' )
        h3Host = self.addHost( 'h3' )
	h4Host = self.addHost( 'h4' )
        
        s1Switch = self.addSwitch( 's1' )
        s2Switch = self.addSwitch( 's2' )
        s3Switch = self.addSwitch( 's3' )
        s4Switch = self.addSwitch( 's4' )

        # Add links
        self.addLink( h1Host, s1Switch )
        self.addLink( h2Host, s2Switch )
        self.addLink( h3Host, s3Switch )
	self.addLink( h4Host, s4Switch )
        self.addLink( s1Switch, s2Switch, delay='1000ms' )
	self.addLink( s1Switch, s3Switch )
	self.addLink( s1Switch, s4Switch )        
	self.addLink( s2Switch, s3Switch )
	self.addLink( s2Switch, s4Switch )        
	self.addLink( s3Switch, s4Switch )
	
        
topos = { 'mytopo': ( lambda: MyTopo() ) }

