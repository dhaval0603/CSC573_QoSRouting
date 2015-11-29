"""
Start and run proto-x on OpenFlow Switches.

"""

from pox.core import core
import pox.openflow.libopenflow_01 as of
from pox.lib.util import dpid_to_str, dpidToStr, strToDPID
from pox.lib.revent import *
import pox.lib.packet as pkt
from pox.lib.packet import *
from pox.lib.recoco import Timer
import time
from pox.openflow.libopenflow_01 import *
import calendar
from time import gmtime
from collections import defaultdict
from pox.openflow.discovery import Discovery
 
log = core.getLogger()
dpids = []
ports = {}
LAT_TYPE = 0x07c3
dpid_ts = {}
dpid_latency = {}
dpid_stats = {}
latency = 0
bw = 1
tx = 2
prevtx = 3
mac = 4
time_init = int(time.time())
first = 1

# flag for debug prints
swdebug = 0
"""
ports = {'dpid':{port_no:[latency, bandwidth, rx_drops, tx_drops, mac-address]}}

"""

######################################################################################################

class Switch (EventMixin):
  def __init__ (self):
    self.connection = None
    self.dpid = None
    self.ports = None
    self._listeners = None
    self._connected_at = None

  def __repr__ (self):
    return dpid_to_str(self.dpid)

  def _install (self, switch, in_port, out_port, match, buf=None):
    msg = of.ofp_flow_mod()
    msg.match = match
    msg.match.in_port = in_port
    msg.idle_timeout = FLOW_IDLE_TIMEOUT
    msg.hard_timeout = FLOW_HARD_TIMEOUT
    msg.actions.append(of.ofp_action_output(port=out_port))
    msg.buffer_id = buf
    switch.connection.send(msg)

  def _install_path (self, p, match, packet_in=None):
    wp = WaitingPath(p, packet_in)
    for sw, in_port, out_port in p:
      self._install(sw, in_port, out_port, match)
      msg = of.ofp_barrier_request()
      sw.connection.send(msg)
      wp.add_xid(sw.dpid, msg.xid)

  def install_path (self, dst_sw, last_port, match, event, tos):
    """
    Attempts to install a path between this switch and some destination
    """
    print "##########Install Path Started##########"
    p = _get_path(self, dst_sw, event.port, last_port, tos)
    if p is None:
      log.warning("Can't get from %s to %s", match.dl_src, match.dl_dst)

      import pox.lib.packet as pkt

      if (match.dl_type == pkt.ethernet.IP_TYPE and
          event.parsed.find('ipv4')):
        # It's IP -- let's send a destination unreachable
        log.debug("Dest unreachable (%s -> %s)",
                  match.dl_src, match.dl_dst)

        from pox.lib.addresses import EthAddr
        e = pkt.ethernet()
        e.src = EthAddr(dpid_to_str(self.dpid))  # FIXME: Hmm...
        e.dst = match.dl_src
        e.type = e.IP_TYPE
        ipp = pkt.ipv4()
        ipp.protocol = ipp.ICMP_PROTOCOL
        ipp.srcip = match.nw_dst  # FIXME: Ridiculous
        ipp.dstip = match.nw_src
        icmp = pkt.icmp()
        icmp.type = pkt.ICMP.TYPE_DEST_UNREACH
        icmp.code = pkt.ICMP.CODE_UNREACH_HOST
        orig_ip = event.parsed.find('ipv4')

        d = orig_ip.pack()
        d = d[:orig_ip.hl * 4 + 8]
        import struct
        d = struct.pack("!HH", 0, 0) + d  # FIXME: MTU
        icmp.payload = d
        ipp.payload = icmp
        e.payload = ipp
        msg = of.ofp_packet_out()
        msg.actions.append(of.ofp_action_output(port=event.port))
        msg.data = e.pack()
        self.connection.send(msg)

      return

    log.debug("Installing path for %s -> %s %04x (%i hops)",
        match.dl_src, match.dl_dst, match.dl_type, len(p))

    # We have a path -- install it
    self._install_path(p, match, event.ofp)
    print "##########Install Path Ended##########"
    # Now reverse it and install it backwards
    # (we'll just assume that will work)
#    p = [(sw,out_port,in_port) for sw,in_port,out_port in p]
#    self._install_path(p, match.flip())


  def _handle_PacketIn (self, event):
    def flood ():
      """ Floods the packet """
      if self.is_holding_down:
        log.warning("Not flooding -- holddown active")
      msg = of.ofp_packet_out()
      # OFPP_FLOOD is optional; some switches may need OFPP_ALL
      msg.actions.append(of.ofp_action_output(port=of.OFPP_FLOOD))
      msg.buffer_id = event.ofp.buffer_id
      msg.in_port = event.port
      self.connection.send(msg)

    def drop ():
      # Kill the buffer
      if event.ofp.buffer_id is not None:
        msg = of.ofp_packet_out()
        msg.buffer_id = event.ofp.buffer_id
        event.ofp.buffer_id = None  # Mark is dead
        msg.in_port = event.port
        self.connection.send(msg)

    packet = event.parsed

    loc = (self, event.port)  # Place we saw this ethaddr
    oldloc = mac_map.get(packet.src)  # Place we last saw this ethaddr
    #print "We got old loc from somewhere" 
    #print oldloc
    ################################################################ Handle LLDP Type ################################################################

    if packet.effective_ethertype == packet.LLDP_TYPE:
      drop()
      return
    
    ##################################################################################################################################################
    ################################################################ Handle ARP Type ################################################################
    if packet.type == ethernet.ARP_TYPE:
      print  "-----------------------ARP REQUEST RECEIVED-----------"
      arppack = packet.find('arp')
      if arppack.opcode == arp.REQUEST:
        print arppack  
        print "loc - " + str(loc)
        mac_map[packet.src] = loc  # Learn position for ethaddr (host port eth address[dp id ,port])
        print "MAC MAP - " + str(mac_map)
        print "Learned "+ str(packet.src)+" at "+   str(loc[0])+"."+ str(loc[1])
        for switch in dpids:
          if switch is self.dpid:
            if swdebug:
              print "Same switch"
            continue
          if swdebug:
            print "Sending ARP REQ to", dpidToStr(switch)
          action_out = [of.ofp_action_output(port=port) for port in host_ports[switch]]
          core.openflow.sendToDPID(switch, of.ofp_packet_out(data=packet.pack(), action=action_out))
        return
      if arppack.opcode == arp.REPLY:
        mac_map[packet.src] = loc
        loc_dst = mac_map[packet.dst]  # Learn position for ethaddr
        if swdebug:
          print "Send reply to DPID - ", str(loc_dst[0]), "port -", loc_dst[1]
        if swdebug:
          print "Type - ", type(str(loc_dst[0]))
        action_out = of.ofp_action_output(port=loc_dst[1])
        core.openflow.sendToDPID(strToDPID(str(loc_dst[0])), of.ofp_packet_out(data=packet.pack(), action=action_out))
        return

    ################################################################# Handle LAT Type ################################################################

    if packet.effective_ethertype == LAT_TYPE:  ####Get type from deeper header.
      """
      Handle incoming latency packets
      """
      # Latency packet is going from SWDSrc to SWDDest from Port. Left at time T
      port = packet.src
      [swdpdest, swdpsrc, port_mac, prevtime] = packet.payload.split(',')
      prevtime = float(prevtime)
      currtime = time.time()
      if swdebug:
         print "######## LATENCY Packet Received ##############"
         print "Packet Source" + str(swdpsrc)
         print "Packet Destination" + str(swdpdest)
         print "PrevTime = ", prevtime, "    CurrTime = ", currtime
      dest_dpid = dpidToStr(event.dpid)
      if dest_dpid == swdpdest:
        # Latency packet travels = Controller - switchSrc - switchDest - Controller
        # Hence Latency = Total Time - (Controller-switch link latencies denoted by dpid_latency) 
        latency = round((((currtime - prevtime) * 1000) - dpid_latency[strToDPID(swdpsrc)] - dpid_latency[event.dpid]), 4)
       # 
        swd = ports[swdpsrc]
        for k in swd:
          if swd[k][4] == port_mac:
            break
        if latency >= 0:
          if k in ports[swdpsrc]:
            ports[swdpsrc][k][0] = latency
        if swdebug:
            print "Latency = " + str(latency)    
            print "######## LATENCY Packet Sent ##############"      
      return
    ##################################################################################################################################################

    if oldloc is None:
      if packet.src.is_multicast == False:
        mac_map[packet.src] = loc  # Learn position for ethaddr
        log.debug("Learned %s at %s.%i", packet.src, loc[0], loc[1])
    elif oldloc != loc:
      drop()
      return
      # ethaddr seen at different place!
      if core.openflow_discovery.is_edge_port(loc[0].dpid, loc[1]):
        # New place is another "plain" port (probably)
        log.debug("%s moved from %s.%i to %s.%i?", packet.src,
                  dpid_to_str(oldloc[0].dpid), oldloc[1],
                  dpid_to_str(loc[0].dpid), loc[1])
        if packet.src.is_multicast == False:
          mac_map[packet.src] = loc  # Learn position for ethaddr
          log.debug("Learned %s at %s.%i", packet.src, loc[0], loc[1])
      elif packet.dst.is_multicast == False:
        # New place is a switch-to-switch port!
        # Hopefully, this is a packet we're flooding because we didn't
        # know the destination, and not because it's somehow not on a
        # path that we expect it to be on.
        # If spanning_tree is running, we might check that this port is
        # on the spanning tree (it should be).
        if packet.dst in mac_map:
          # Unfortunately, we know the destination.  It's possible that
          # we learned it while it was in flight, but it's also possible
          # that something has gone wrong.
          if swdebug:
            print "Hit MacMap"
          log.warning("Packet from %s to known destination %s arrived "
                      "at %s.%i without flow", packet.src, packet.dst,
                      dpid_to_str(self.dpid), event.port)


    if packet.dst.is_multicast:
      log.debug("Flood multicast from %s", packet.src)
      flood()
    else:
      if packet.dst not in mac_map:
        log.debug("%s unknown -- flooding" % (packet.dst,))
        flood()
      else:
        dest = mac_map[packet.dst]
	if packet.type == ethernet.IP_TYPE:
           ipv4_packet = packet.find("ipv4")
           tos = ipv4_packet.tos
           if packet.find("icmp"):
             print "ICMP packet received"
           print "Received ToS = ", tos
     	match = of.ofp_match.from_packet(packet)
	self.install_path(dest[0], dest[1], match, event, tos)
     
  def disconnect (self):
    global switch_neighbourhood
    if self.connection is not None:
      del_dpid = self.connection.dpid
      del ports[dpidToStr(del_dpid)]
      del dpids[dpids.index(del_dpid)]
      del dpid_ts[del_dpid]
      del dpid_latency[del_dpid]
      del dpid_stats[del_dpid]
     # for k in switch_neighbourhood:
      switch_neighbourhood = {}
      create_neighbourhood_matrix()
      log.debug("Disconnect %s" % (self.connection,))
      self.connection.removeListeners(self._listeners)
      self.connection = None
      self._listeners = None

  def connect (self, connection):
    if self.dpid is None:
      self.dpid = connection.dpid
    assert self.dpid == connection.dpid
    if self.ports is None:
      self.ports = connection.features.ports
    self.disconnect()
    log.debug("Connect %s" % (connection,))
    self.connection = connection
    self._listeners = self.listenTo(connection)
    self._connected_at = time.time()

  @property
  def is_holding_down (self):
    if self._connected_at is None: return True
    # print (time.time())
    if time.time() - self._connected_at > FLOOD_HOLDDOWN:
      return False
    return True

  def _handle_ConnectionDown (self, event):
    self.disconnect()

####################################### Link Stats Calculation ##########################################

def handle_switch_desc(event):
  currtime = time.time()
  prevtime = dpid_ts[event.dpid]
  latency = round(((currtime - prevtime) * 1000), 4)
  dpid_latency[event.dpid] = latency / 2

def create_lat_pkt(dpid, port, port_mac):
  latPacket = pkt.ethernet(type=LAT_TYPE)
  latPacket.src = port_mac
  for x in core.openflow_discovery.adjacency:
      if ((x.dpid1 == dpid) and (x.port1 == port)):
        # print "Sending for",l
        latPacket.dst = pkt.ETHERNET.NDP_MULTICAST  # need to decide
        latPacket.payload = dpidToStr(x.dpid2) + ',' + dpidToStr(x.dpid1) + ',' + port_mac + ',' + str(time.time())  ####as per paper
        return latPacket.pack()

def find_latency_c_to_sw(dpid):
  pkt = ofp_stats_request(type=OFPST_DESC)
  dpid_ts[dpid] = time.time()
  core.openflow.sendToDPID(dpid, pkt)  ####to get stats.
  mbody = ofp_queue_stats_request()
  mbody.port_no = of.OFPP_NONE  ####to get statistics for all ports.
  pkt = ofp_stats_request(body=mbody)
  pkt.type = OFPST_QUEUE  ####Queue Stats
  core.openflow.sendToDPID(dpid, pkt)
  
def find_latency_sw_to_sw(dpid):
  for key in ports[dpidToStr(dpid)]:  ####latency packet to all ports.
    if(key != 65534):  ####except local port denoted by 65534.
      packet = of.ofp_packet_out(action=of.ofp_action_output(port=key))
      packet.data = create_lat_pkt(dpid, key, ports[dpidToStr(dpid)][key][4])
      core.openflow.sendToDPID(dpid, packet)
  
def _handle_portstats_received (event):
    for pStat in event.stats:
        if pStat.port_no in ports[dpidToStr(event.dpid)]:
          qSt = pStat.tx_dropped - ports[dpidToStr(event.dpid)][pStat.port_no][prevtx]
          ports[dpidToStr(event.dpid)][pStat.port_no][prevtx] = pStat.tx_dropped
          ports[dpidToStr(event.dpid)][pStat.port_no][tx] = qSt
          if swdebug:
              print "DPID-Port ", event.dpid, " - ", pStat.port_no
              print "TX Packets dropped in this interval ", ports[dpidToStr(event.dpid)][pStat.port_no][tx] 
        
######################################################################################################

def find_latency():
  create_neighbourhood_matrix();
  if swdebug:
    print "Here are the link status"
    print ports
  for dpid in dpids:
    find_latency_c_to_sw(dpid)
  for dpid in dpids:
    find_latency_sw_to_sw(dpid)
    
def find_queue_drops():
    for connection in core.openflow._connections.values():
      connection.send(of.ofp_stats_request(body=of.ofp_port_stats_request()))
      
#### Qos Index Calculator Module #####

QOS_indices = {}
switch_neighbourhood = {}
# Declaring a dictionary for weights for different traffic to calculate QOS Index.
QOS_weights = {}

# Constants K1, K2, K3 for QOS index for diffent kinds of traffic.
voice_traffic = [100, 0.1, 0.003]
video_traffic = [100, 0.07, 0.007]
critical_traffic = [100, 0.01, 0.01]
best_effort_traffic = [100, 0, 0]

# Polpulating the weights dictionary
QOS_weights['voice'] = voice_traffic
QOS_weights['video'] = video_traffic
QOS_weights['critical'] = critical_traffic
QOS_weights['best_effort'] = best_effort_traffic

# TOS values for different kinds of traffic.
tos_voice = [184, 160, 128, 152, 144]
tos_video = [120, 112]
tos_critical = [80, 88, 48, 56]
tos_best_effort = [0]
host_ports = {}  

def create_neighbourhood_matrix():
  # switch_neighbourhood = {}
  #print "##########Create Adjacency Started##########"
  for dpid in dpids:  ####For every switch, create empty adjacency
    switch_neighbourhood[dpid] = {}
  for l in core.openflow_discovery.adjacency:
    # print "Value of L is - " + str(l)  
    switch_neighbourhood[l.dpid1][l.port1] = l.dpid2
  if swdebug:
    print "Adjacency of the Topology", switch_neighbourhood
  #print "##########Create Adjacency Ended##########"  
    

def calc_qos_index(traffic_type):
  #print "##########Find Cost Started##########"
  if swdebug:
    print "Received ToS IN CALCULATE PATH = ", traffic_type
  create_neighbourhood_matrix()
  tos_constants = QOS_weights[traffic_type]
  if swdebug:
    print "Initialized constants. Finding cost now"
  sw_latency_map = {}
  QOS_indices[traffic_type] = {}

  for switch in dpids:
    QOS_indices[switch] = {}
    switchStr = dpidToStr(switch)
    sw_latency_map = ports[switchStr]
    port_list = []
    neighbors_dict = {}
    for port in switch_neighbourhood[switch]:
      port_list = sw_latency_map[port]
      dest_switch = switch_neighbourhood[switch][port]

      l = port_list[latency]
      b = port_list[bw]
      t = port_list[tx]

      n = 0
      if traffic_type is not "best_effort":
        n = 1
      cost = tos_constants[0] / b + n * (tos_constants[1] * l + tos_constants[2] * t)
      neighbors_dict[dest_switch] = cost
    QOS_indices[traffic_type][switch] = neighbors_dict
  return QOS_indices[traffic_type]
  #print "##########Find Cost Ended##########"

##### Qos Index Calculator Module ####

####################################### Floyd Warshall ###############################################

log = core.getLogger()

# Adjacency map.  [sw1][sw2] -> port from sw1 to sw2
adjacency = defaultdict(lambda:defaultdict(lambda:None))

# Switches we know of.  [dpid] -> Switch
switches = {}

# ethaddr -> (switch, port)
mac_map = {}

# [sw1][sw2] -> (distance, intermediate)
path_map = defaultdict(lambda:defaultdict(lambda:(None, None)))

# Waiting path.  (dpid,xid)->WaitingPath
waiting_paths = {}

# Time to not flood in seconds
FLOOD_HOLDDOWN = 5

# Flow timeouts
FLOW_IDLE_TIMEOUT = 10
FLOW_HARD_TIMEOUT = 0

# How long is allowable to set up a path?
PATH_SETUP_TIME = 4


def _calculate_route (tos):
  """
  Essentially Floyd-Warshall algorithm
  """
  print "===============================================TOS is ==========================" + str(tos)
  # Get the traffic_type of tos
  traffic_type = None
  if tos in tos_video:
    traffic_type = "video"
  elif tos in tos_voice:
    traffic_type = "voice"
  elif tos in tos_critical:
    traffic_type = "critical"
  else:
    traffic_type = "best_effort"
  if swdebug:
    print "***************************************************************************"
    print "***************************************************************************"
    print "Finding costs for this tos value"
  costs = calc_qos_index(traffic_type)
  if swdebug:
    print costs
    print "Now running Floyd Warshall"
  def dump ():
    for i in sws:
      for j in sws:
        a = path_map[i][j][0]
        # a = adjacency[i][j]
        if a is None: a = "*"
      #  print a,
     # print

  sws = switches.values()
  path_map.clear()
  for k in sws:
    for j, port in adjacency[k].iteritems():
      if port is None: continue
      kdpid = k.dpid
      jdpid = j.dpid
      cost = costs[kdpid][jdpid]
      path_map[k][j] = (cost, None)
    path_map[k][k] = (0, None)  # distance, intermediate

  # dump()

  for k in sws:
    for i in sws:
      for j in sws:
        if path_map[i][k][0] is not None:
          if path_map[k][j][0] is not None:
            # i -> k -> j exists
            ikj_dist = path_map[i][k][0] + path_map[k][j][0]
            if path_map[i][j][0] is None or ikj_dist < path_map[i][j][0]:
              # i -> k -> j is better than existing
              path_map[i][j] = (ikj_dist, k)
              
  if swdebug:
    print "End of Floyd Warshall"
    print "***************************************************************************"
    print "***************************************************************************"
  # dump()

######################################################################################################

def _get_raw_path (src, dst, tos):
  """
  Get a raw path (just a list of nodes to traverse)
  """
  if len(path_map) == 0: _calculate_route(tos) 
  # _calculate_route(tos)
  if src is dst:
    # We're here!
    return []
  if path_map[src][dst][0] is None:
    return None
  intermediate = path_map[src][dst][1]
  if intermediate is None:
    # Directly connected
    return []
  return _get_raw_path(src, intermediate, tos) + [intermediate] + \
         _get_raw_path(intermediate, dst, tos)

######################################################################################################

def _check_path (p):
  """
  Make sure that a path is actually a string of nodes with connected ports

  returns True if path is valid
  """
  for a, b in zip(p[:-1], p[1:]):
    if adjacency[a[0]][b[0]] != a[2]:
      return False
    if adjacency[b[0]][a[0]] != b[1]:
      return False
  return True

######################################################################################################

def _get_path (src, dst, first_port, final_port, tos):
  """
  Gets a cooked path -- a list of (node,in_port,out_port)
  """
  _calculate_route(tos)
  # Start with a raw path...
  if src == dst:
    path = [src]
  else:
    path = _get_raw_path(src, dst, tos)
    if path is None: return None
    path = [src] + path + [dst]

  # Now add the ports
  r = []
  in_port = first_port
  for s1, s2 in zip(path[:-1], path[1:]):
    out_port = adjacency[s1][s2]
    r.append((s1, in_port, out_port))
    in_port = adjacency[s2][s1]
  r.append((dst, in_port, final_port))
  if swdebug:
    print "Printing path\n", r
  assert _check_path(r), "Illegal path!"

  return r

######################################################################################################
class WaitingPath (object):
  """
  A path which is waiting for its path to be established
  """
  def __init__ (self, path, packet):
    """
    xids is a sequence of (dpid,xid)
    first_switch is the DPID where the packet came from
    packet is something that can be sent in a packet_out
    """
    self.expires_at = time.time() + PATH_SETUP_TIME
    self.path = path
    self.first_switch = path[0][0].dpid
    self.xids = set()
    self.packet = packet

    if len(waiting_paths) > 1000:
      WaitingPath.expire_waiting_paths()

  def add_xid (self, dpid, xid):
    self.xids.add((dpid, xid))
    waiting_paths[(dpid, xid)] = self

  @property
  def is_expired (self):
    return time.time() >= self.expires_at

  def notify (self, event):
    """
    Called when a barrier has been received
    """
    self.xids.discard((event.dpid, event.xid))
    if len(self.xids) == 0:
      # Done!
      if self.packet:
        log.debug("Sending delayed packet out %s"
                  % (dpid_to_str(self.first_switch),))
        msg = of.ofp_packet_out(data=self.packet,
            action=of.ofp_action_output(port=of.OFPP_TABLE))
        core.openflow.sendToDPID(self.first_switch, msg)

      core.l2_multi.raiseEvent(PathInstalled(self.path))


  @staticmethod
  def expire_waiting_paths ():
    packets = set(waiting_paths.values())
    killed = 0
    for p in packets:
      if p.is_expired:
        killed += 1
        for entry in p.xids:
          waiting_paths.pop(entry, None)
    if killed:
      log.error("%i paths failed to install" % (killed,))


######################################################################################################
####################################### Class PathInstall ############################################

class PathInstalled (Event):
  """
  Fired when a path is installed
  """
  def __init__ (self, path):
    self.path = path


######################################################################################################
####################################### Forwarding Class #############################################

class l2_multi (EventMixin):

  _eventMixin_events = set([
    PathInstalled,
  ])

  def __init__ (self):
    # Listen to dependencies (specifying priority 0 for openflow)
    core.listen_to_dependencies(self, listen_args={'openflow':{'priority':0}})

  def _handle_openflow_discovery_LinkEvent (self, event):
    def flip (link):
      return Discovery.Link(link[2], link[3], link[0], link[1])

    l = event.link
    sw1 = switches[l.dpid1]
    sw2 = switches[l.dpid2]

    # Invalidate all flows and path info.
    # For link adds, this makes sure that if a new link leads to an
    # improved path, we use it.
    # For link removals, this makes sure that we don't use a
    # path that may have been broken.
    # NOTE: This could be radically improved! (e.g., not *ALL* paths break)
    clear = of.ofp_flow_mod(command=of.OFPFC_DELETE)
    for sw in switches.itervalues():
      if sw.connection is None: continue
      sw.connection.send(clear)
    path_map.clear()
    if event.removed:
      # This link no longer okay
      if sw2 in adjacency[sw1]: del adjacency[sw1][sw2]
      if sw1 in adjacency[sw2]: del adjacency[sw2][sw1]

      # But maybe there's another way to connect these...
      for ll in core.openflow_discovery.adjacency:
        if ll.dpid1 == l.dpid1 and ll.dpid2 == l.dpid2:
          if flip(ll) in core.openflow_discovery.adjacency:
            # Yup, link goes both ways
            adjacency[sw1][sw2] = ll.port1
            adjacency[sw2][sw1] = ll.port2
            # Fixed -- new link chosen to connect these
            break
      # Update "Ports" and remove entries for this port
      if swdebug:
        print "Link removed", l
      for switch in ports:
        for port in ports[switch]:
          if port is l.port1:
            if swdebug:
              print "Port removed = ", port
            del ports[switch][port]
            break

      if swdebug:
        print "****************** Printing ports after Link removed event ****************"
        print ports
        print "***************************************************************************"
    else:
      # If we already consider these nodes connected, we can
      # ignore this link up.
      # Otherwise, we might be interested...
      # if event.added:
      #  ports[dpidToStr(l.dpid1)][l.port1] = [0.0, 100, 0, 0]
      #  ports[dpidToStr(l.dpid2)][l.port2] = [0.0, 100, 0, 0]
      if adjacency[sw1][sw2] is None:
        # These previously weren't connected.  If the link
        # exists in both directions, we consider them connected now.
        if flip(l) in core.openflow_discovery.adjacency:
          # Yup, link goes both ways -- connected!
          adjacency[sw1][sw2] = l.port1
          adjacency[sw2][sw1] = l.port2

      # If we have learned a MAC on this port which we now know to
      # be connected to a switch, unlearn it.
      bad_macs = set()
      for mac, (sw, port) in mac_map.iteritems():
        if sw is sw1 and port == l.port1: bad_macs.add(mac)
        if sw is sw2 and port == l.port2: bad_macs.add(mac)
      for mac in bad_macs:
        log.debug("Unlearned %s", mac)
        del mac_map[mac]

######################################################################################################

  def _handle_openflow_ConnectionUp (self, event):
    #print "##########Connection Up Event Started##########"
    sw = switches.get(event.dpid)
    if swdebug:
      print "DPID for", event.dpid, " = ", dpidToStr(event.dpid)
    if sw is None:
      # New switch
      sw = Switch()
      sw.dpid = event.dpid
      switches[event.dpid] = sw
      sw.connect(event.connection)
    else:
      sw.connect(event.connection)
    
    dpids.append(event.dpid)
    ports[dpidToStr(event.dpid)] = {}
    if swdebug:
      print "Add ports to link status DB"
    dpid_ts[event.dpid] = 0.000
    dpid_latency[event.dpid] = 0.000  # Latency for Controller-Switch Link
    dpid_stats[event.dpid] = []
    for p in event.ofp.ports:
      port = [0.0, 100, 0, 0, str(p.hw_addr)]
      ports[dpidToStr(event.dpid)][p.port_no] = port
    print "Ports dictionary - " + str(ports)

    """
    Tell all switches to forward latency packets to controller
    """
    if swdebug:
      print "Connected to switch", dpidToStr(event.dpid)
    connection = event.connection
    match = of.ofp_match(dl_type=LAT_TYPE, dl_dst=pkt.ETHERNET.NDP_MULTICAST)
    msg = of.ofp_flow_mod()
    msg.priority = 65535
    msg.match = match
    msg.idle_timeout = 0
    msg.hard_timeout = 0
    msg.actions.append(of.ofp_action_output(port=of.OFPP_CONTROLLER))
    connection.send(msg)
    #print "##########Connection Up Event Ended##########"
######################################################################################################

  def _handle_openflow_BarrierIn (self, event):
    wp = waiting_paths.pop((event.dpid, event.xid), None)
    if not wp:
      # log.info("No waiting packet %s,%s", event.dpid, event.xid)
      return
    # log.debug("Notify waiting packet %s,%s", event.dpid, event.xid)
    wp.notify(event)

######################################################################################################
# Timer(10, find_latency, recurring = True)
######################################################################################################
###################################### Launch Function ###############################################

def GetTopologyParams():
  ########Start 3 timers. TO_FIND_LATENCY, WAITING_PATH, FIND_PORTS_ON_SWITCH_WHERE_HOSTS_ARE_PRESENT
  #print "##########Find Latency Timer (Recurring) Started##########"
  Timer(10, find_latency, recurring=True)
  #print "##########Find Latency Timer Ended##########"
  
  #print "##########Find Queue Drop Timer (Recurring) Started##########"
  Timer(10, find_queue_drops, recurring=True)
  #print "##########Find Queue Drop Timer Ended##########"
  
  #Timer(10, find_bandwidth, recurring=True)
  
  timeout = min(max(PATH_SETUP_TIME, 5) * 2, 15)
  Timer(timeout, WaitingPath.expire_waiting_paths, recurring=True)
  
  Timer(10, sw_HostPorts, recurring=True)
  
def sw_HostPorts ():  ####for ARP requests. Has dpid as key and ports as values.(Function can be relocated)
  # print ports
  #print "##########Find Host Ports Started##########"
  create_neighbourhood_matrix()
  for dpid in dpids:
    host_ports[dpid] = []
    for p in ports[dpidToStr(dpid)].keys():
      if p == 65534:
        continue
      flag = False
      for switchp in switch_neighbourhood[dpid].keys():
        if p is switchp:
          flag = True
      if flag is False:
        host_ports[dpid].append(p)
    if swdebug:
      print "Host port for", dpidToStr(dpid), " = ", host_ports[dpid] 
  #print "##########Find Host Ports Ended##########"  

def launch ():
  from pox.openflow.discovery import launch
  launch()
  def start_launch ():
    core.registerNew(l2_multi)
    core.openflow.addListenerByName("SwitchDescReceived", handle_switch_desc)
    core.openflow.addListenerByName("PortStatsReceived", _handle_portstats_received)
    print "Qos Based Optimal Routing using OpenFlow"
    log.debug("Latency monitor running")
    GetTopologyParams()

  core.call_when_ready(start_launch, "openflow_discovery")

######################################################################################################
