import network

# Configurar la interfaz Wi-Fi
wifi = network.WLAN(network.STA_IF)
wifi.active(True)

# Obtener la dirección MAC
mac_address = wifi.config('mac')
print("Dirección MAC del Raspberry Pi Pico W:", ':'.join(['{:02x}'.format(b) for b in mac_address]))