import uuid
from pygatt.backends import BLEAddressType
from pygatt import BGAPIBackend
import logging
log = logging.getLogger(__name__)

#The class mimics the behavior of a Serial object so that much of the same code can be used by the client
#It relies on the pygatt library which implements services provided by a BGAPI compatible adapter
#The BGAPI adapter communicates with BLE devices and provides serial port connection with the client
#This class specifically implements communication with nRF52XX BLE-enabled microcontroller
class bleuart():
    def __init__(self, port):     
        self.port = port                                #COM port of the BGAPI adapter
        self._adapter = BGAPIBackend(port)              #adapter object
        self._address_type = BLEAddressType.random      #BLE address type used by nRF52
        self._rssi = 0                                  #device signal strength
        self._connected = False                         #track connection status
        self._started = False                           #track adapter status
        self._subscribed_NUS_TX = False                 #track subscrition status for output
        self._subscribed_NUS_RX = False                 #track subscrition status for input
        self._scan_time = 2                             #seconds to search for devices
        self._handle_TX = 0                             #handle for output service
        self._handle_RX = 0                             #handle for input service    
        self._devices = []                              #list of devices found after scan
        self._Characteristics = []                      #list of characteristics in a device
        self._value = bytearray([24])                   #incoming data from device
        self._buffer = []                               #buffer for outgoing data to client

        #Characteristic IDs for Nordic UART Service (Outgoing and Incoming)
        self._NUS_TX = uuid.UUID('6e400003-b5a3-f393-e0a9-e50e24dcca9e')
        self._NUS_RX = uuid.UUID('6e400002-b5a3-f393-e0a9-e50e24dcca9e')
        
    #stop the adapter if class is deleted
    def __del__(self):
        if self._started:
            self._stop()

    #start the BGAPI adapter    
    def _start(self):
        if not self._started:
            try:
                self._adapter.start()
            except Exception as e:
                log.error("Connection to BGAPI adapter failed - {}".format(e))
            else:
                self._started = True
                log.info('Started BGAPI adapter')

    #stop the BGAPI adapter        
    def _stop(self):
        if self._started:
            self._adapter.stop()
            self._started = False
            log.info('Stopped BGAPI adapter')
    
    #Analogue to Serial.serial.close()
    #End subscriptions, disconnect, and stop adapter
    def close(self):
        self._unsubscribe()
        self.disconnect()
        self._stop()

    #Scan for nearyby devices, set instance variable    
    def _scan(self):
        if not self._started:
            self._start()
        if not self._devices:
            self._devices = self._adapter.scan(timeout=self._scan_time)
    
    #Scan for nearyby devices, return list to client
    def scan(self):
        if not self._started:
            self._start()
        if not self._devices:
            self._devices = self._adapter.scan()
        return(self._devices)        
    
    #Find the address of the named device from device list
    def _get_address(self):
        #check is device is needed
        if not self._address:
            #get device list
            if not self._devices:    
                self._scan()
            
            #Match device name to list of devices
            try:
                self._address = list(filter(lambda device: device['name'] == self.name, self._devices))[0]["address"]
            
            #Tell user device was not found
            except Exception as e:
                self._stop()
                self._started = False
                log.error('Device {} was not found - {}'.format(self.name, e))
                raise ValueError('Device {} was not found - {}'.format(self.name, e))
            
            #Tell user device adress was found
            else:
                log.info('Found {} at address {}'.format(self.name, self._address))
        else:
            log.info('Connecting to device at {}...'.format(self._address))

    #Connoect to the BLE device    
    def connect(self, name = None, address = None, mode = 'r'):
        #Start the BGAPI adapter
        if not self._started:
            self._start()
        
        #Check that name or address is available
        if not name and not address:
            log.error('The device name and address cannot both be None')
            raise ValueError('The device name and address cannot both be None')
        else:
            self.name = name
            self._address = address
        
        #Find device address
        self._get_address()
        
        #Attempt to connect
        try:
            self._device = self._adapter.connect(self._address, timeout=15,address_type=self._address_type)
        
        #If connection failes, let user know
        except Exception as e:
            self._connected = False
            self._stop()
            self._started = False
            log.error('Connection to device {} at {} failed - {}'.format(self.name, self._address, e))
            raise ValueError('Connection to device {} at {} failed - {}'.format(self.name, self._address, e))
        
        #if successful, tell user, then subsscirbe to services
        else:
            log.info('Connected to {} at {}'.format(self.name, self._address))
            self._connected = True
            self._rssi = self._device.get_rssi()
            self._handle_TX = self._device.get_handle(self._NUS_TX)
            self._handle_RX = self._device.get_handle(self._NUS_RX)
            
            #'r' = read, or sending to client the outgoing data on device
            if 'r' in mode:
                self._subscribe_NUS_TX()
            
            #'w' = write, or sending from client the incoming data on device
            # Note that write is not fully implemented
            if 'w' in mode:
                self._subscribe_NUS_RX()

    #End a connection to a device        
    def disconnect(self):
        if self._connected:
            self._connected = False
            self._device.disconnect()
            log.info('Disconnected from {} at {}'.format(self.name, self._address))
            
    #Return the signal strenght of the connected device
    def get_rssi(self) :
        if not self._connected:
            log.error('Device not connected, cannot determine rssi')
        else:
            log.info('Device {}: rssi = {}'.format(self.name, self._rssi))
            return(self._rssi)
    
    ###Services on the nRF52XX
    #NUS = Nordic UART Service, a communication protocol that looks similar to a serial port
    
    #This subscribes to the service that transmits data from device to the client
    def _subscribe_NUS_TX(self):
        
        #Attempt to subscribe
        #Callback to handle incoming data is ._receive
        try:
            self._device.subscribe(self._NUS_TX, callback=self._receive)
        
        #Check if service characteristic is available
        except Exception as e:
            #Get characteristics for device
            if not self._Characteristics:
                self._Characteristics = self._device.discover_characteristics()
                log.debug('Discovered device characteristics')
            
            #Search for service
            for UUID in self._Characteristics:
                if UUID == self._NUS_TX:
                    err = 'Failed to subscribe to NUS_TX UUID{{{}}} - {}'.format(self._NUS_TX,e)
                    break
            
            #Tell user subsription failed because service was not available
            else:
                err = 'Failed to subscribe since NUS_TX UUID{{{}}} was not found on this device - {}'.format(self._NUS_TX, e) 
            
            #Log error, end connection, and stop adapter
            log.error(err)
            self._subscribed_NUS_TX = False
            self._connected = False
            self._started = False
            self._stop()
            log.error('BGAPI adapter stopped')
            raise ValueError(err)
        
        #Tell user subsscription was successful
        else:
            self._subscribed_NUS_TX = True
            log.info('Subscribed to NUS_TX UUID{{{}}}'.format(self._NUS_TX))
    
    #This subscribes to the service that receives data from client to the device
    def _subscribe_NUS_RX(self):
        #Attempt to subscribe
        try:
            self._device.subscribe(self._NUS_RX)
        
        #Check if service characteristic is available
        except Exception as e:
            #Get characteristics for device
            if not self._Characteristics:
                self._Characteristics = self._device.discover_characteristics()
                log.debug('Discovered device characteristics')
            
            #Search for service
            for UUID in self._Characteristics:
                if UUID == self._NUS_RX:
                    err = 'Failed to subscribe to NUS_RX UUID{{{}}} - {}'.format(self._NUS_RX,e)
                    break
            
            #Tell user subsription failed because service was not available
            else:
                err = 'Failed to subscribe since NUS_TX UUID{{{}}} was not found on this device - {}'.format(self._NUS_RX, e)
            
            #Log error, end connection, and stop adapter
            log.error(err)
            self._subscribed_NUS_RX = False
            self._connected = False
            self._started = False
            self._stop()
            log.error('BGAPI adapter stopped')
            raise ValueError(err)
        
        #Tell user subsscription was successful
        else:
            self._subscribed_NUS_RX = True
            log.info('Subscribed to NUS_RX UUID{{{}}}'.format(self._NUS_RX))
    
    #Unsubscribe from both services (by default)
    def _unsubscribe(self, mode = 'rw'):
        if 'r' in mode:
            if self._subscribed_NUS_TX: 
               try:
                    self._device.unsubscribe(self._NUS_TX)
               except Exception as e:
                    log.error('Failed to unsubscribe from NUS_TX UUID{{{}}} - {}'.format(self._NUS_TX,e))
               else:
                    self._subscribed_NUS_TX = False
                    log.info('Unsubscribed from NUS_TX UUID {{{}}}'.format(self._NUS_TX))
        if 'w' in mode:
            if self._subscribed_NUS_RX:
                try:
                    self._device.unsubscribe(self._NUS_RX)
                except Exception as e:
                    log.error('Failed to unsubscribe from NUS_RX UUID{{{}}} - {}'.format(self._NUS_RX,e))
                else:
                    self._subscribed_NUS_RX = False
                    log.info('Unsubscribed from NUS_RX UUID {{{}}}'.format(self._NUS_RX))

    #If the returned value is greater than zero, new output is available
    def inWaiting(self):
        return(len(self._buffer))

    #Write line of data to a device
    #Not implemented    
    def writeline(self, value):
        log.error('writeLine() has not been implemented')
        raise NotImplementedError()

    #Write data to a device
    #Not implemented    
    def write(self, value):
        log.error('write() has not been implemented')
        raise NotImplementedError()
    
    #Pop the front of the buffer and return its value
    def read(self):
        if self._buffer:
            return(self._buffer.pop())

    #When new data is available, insert at back of buffer        
    def _receive(self, handle, value):
        self._buffer.insert(0, value)
    
    #flush is not necessary for BLE, but it is often used with Serial.serial
    def flush(self):
        pass#for serial compatibility
    
    #Clear the input buffer
    def reset_input_buffer(self):
        self._buffer = []

    #Pop the front of the buffer and return its value
    #note that readline does not wait for an endline, since this is the default behavior
    #This is here for Serial.serial compatibility
    def readline(self, max_reads = 5):
        #log.warning('readline() is the same as read')
        if self._buffer:
            return(self._buffer.pop())