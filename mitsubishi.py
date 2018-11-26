import socket
import struct
import sys
from mitsubishi_echonet import eojx, epc, esv, nodes as n

ENL_PORT = 3610
ENL_MULTICAST_ADDRESS = "224.0.23.0"

# Function to construct an Echonet binary message
# data is in following format:
        #  'EHD1':'10'          ECHONET Lite message header 1
        #  'EHD2':'81'          ECHONET Lite message header 2
        #  'TID': '01'          Transaction ID
        #  'SEOJ': '05ff01'   Source ECHONET Lite object specification
        #  'DEOJ': '0ef000'   Destination ECHONET Lite object specification
        #  'ESV': 'Get'          ECHONET Lite service
        #  'OPC':               Array of processing properties
        #
        #   OPC array looks like this: [EPC] [PDC] [EDT]
        #   IF PDC is 00 then No EDT is required.
        #[EHD1] [EHD2] [TID] [ SEOJ ]  [ DEOJ ]  [ESV] [OPC] [EPC] [PDC]
        # 10     81    00 01 05 ff 01  0e f0 00   62    01    d6    00

def BuildEchonetMsg(data):
  try:
      # EHD is fixed to 0x10
      message = 0x1081
      # validate TID (set a default value if none provided)
      if 'TID' not in data:
          data['TID'] = 0x01
      elif data['TID'] > 0xFFFF:
          raise ValueError('Transaction ID is larger then 2 bytes.')
      message = (message << 16) + data['TID']

      # append SEOJ
      message = (message << 24) + 0x05FF01

      # validate DEOJ
      if data['DEOJGC'] in eojx.GROUP:
          message = (message << 8) + data['DEOJGC']
      else:
          raise ValueError('Value ' + str(hex(data['DEOJGC'])) + ' not a valid SEO Group code')

      if data['DEOJCC'] in eojx.CLASS[data['DEOJGC']]:
          message = (message << 8) + data['DEOJCC']
      else:
          raise ValueError('Value ' + str(hex(data['DEOJCC'])) + ' not a valid SEO class code')
      message = (message << 8) + data['DEOJIC']

      # validate esv - it can be either the HEX code
      if data['ESV'] in esv.CODES:
          # ESV code is a string
          message = (message << 8) + data['ESV']
      else:
          raise ValueError('Value not in ESV code table')
      # validate OPC
      message = (message << 8) + len(data['OPC'])

      for values in data['OPC']:
        # validate EPC
        message =  (message << 8) + values['EPC']
        if 'PDC' in values:
            message =  (message << 8) + values['PDC']
        # if PDC has a value then concat EDT to message
            if values['PDC'] > 0:
                message =  (message << 8 * values['PDC']) + values['EDT']
        else:
            message =  (message << 8) + 0x00
      return format(message, 'x')
# some error handling here.
  except ValueError as error:
        print('Caught this error: ' + repr(error))
        quit()

def DecodeEchonetMsg(byte):
  data = {}
  try:
      data['EHD1'] = byte[0]
      if data['EHD1'] not in epc.EHD1:
          raise ValueError('EHD1 Header invalid')
      data['EHD2'] = byte[1]
      if data['EHD2'] not in epc.EHD2:
          raise ValueError('EHD2 Header invalid')
      data['TID'] = int.from_bytes(byte[2:4], byteorder='big')

      # Decode SEOJ
      data['SEOJGC'] = byte[4]
      data['SEOJCC'] = byte[5]
      data['SEOJIC'] = byte[6]

      # Decode DEOJ
      data['DEOJGC'] =  byte[7]
      data['DEOJCC'] =  byte[8]
      data['DEOJIC'] =  byte[9]

      # DEcode Service property
      data['ESV'] =  byte[10]

      i = 0
      epc_pointer = 12
      data['OPC'] = []
      # decode multiple processing properties (OPC)
      while i <  (byte[11]):
          OPC = {}
          pdc_pointer = epc_pointer + 1
          edt_pointer = pdc_pointer + 1
          end_pointer = edt_pointer
          OPC['EPC'] = byte[epc_pointer]
          OPC['PDC'] =  byte[pdc_pointer]
          pdc_len = byte[pdc_pointer]
          end_pointer += pdc_len
          OPC['EDT'] = byte[edt_pointer:end_pointer]
          epc_pointer = end_pointer
          i += 1
          data['OPC'].append(OPC)

  except ValueError as error:
        print('Caught this error: ' + repr(error))
        quit()
  return data


# used for ECHONET Unicast and Multicast transcations
# message is assumed a correctly formatted ECHONET string
# expects multiple data will be received from multiple nodes
def SendMessage(message, ip_address):
    data =[]
    transaction_group = (ip_address, ENL_PORT)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(('',ENL_PORT))
    # Set a timeout so the socket does not block
    # indefinitely when trying to receive data.
    sock.settimeout(0.5)

    # Set the time-to-live for messages to 1 so they do not
    # go past the local network segment.
    ttl = struct.pack('b', 1)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, ttl)

    try:
        sent = sock.sendto(bytearray.fromhex(message), transaction_group)
        # Look for responses from all recipients
        while True:
            try:
                payload, server = sock.recvfrom(1024)
            except socket.timeout:
                break
            else:
                # Received a packet.
                data.append({'server':server,'payload':payload})
    finally:
        sock.close()
    return data

# Discovers echonet nodes on the network
def Discover():
    eoa = []; # array containing echonet objects
    tx_payload = {
        'TID' : 0x01,
        'DEOJGC': 0x0E,
        'DEOJCC': 0xF0,
        'DEOJIC': 0x00,
        'ESV' : 0x62,
        'OPC' : [{'EPC': 0xD6}]
    }
    # Build ECHONET discover messafge.
    message = BuildEchonetMsg(tx_payload)

    # Send message to multicast group and receive data
    data = SendMessage(message, ENL_MULTICAST_ADDRESS);
    # Decide received message for each node discovered:
    for node in data:
        rx = DecodeEchonetMsg(node['payload'])
        if (tx_payload['DEOJGC'] == rx['SEOJGC'] and
        rx['TID'] == tx_payload['TID'] and
        rx['OPC'][0]['EPC'] == 0xd6):
            print('ECHONET lite node discovered at {}'.format(node['server'][0]))
            # Action EDT payload by calling applicable function using lookup table
            edt = epc.CODE[rx['SEOJGC']][rx['SEOJCC']][rx['OPC'][0]['EPC']][1](rx['OPC'][0]['EDT'])
            rx['instance'] = edt['eojci']
            # Hard coding to ensure

            e = epc.CODE[edt['eojgc']][edt['eojcc']]['class'](rx, node['server'][0])

            eoa.append(e)

    return eoa