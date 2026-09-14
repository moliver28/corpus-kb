data = open('.github/workflows/ci.yml', 'rb').read()
null_byte = b'\x00'
print('Disk size:', len(data))
print('Disk null bytes:', data.count(null_byte))
print('Disk last 50:', data[-50:])