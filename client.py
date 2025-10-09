import socket
import threading
from dotenv import load_dotenv
import os

load_dotenv()

client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
print("Connecting to server in address:", os.environ.get('SERVER_ADDRESS'))
client.connect((os.environ.get('SERVER_ADDRESS', '127.0.0.1'), 3389))

def receive_messages(sock:socket.socket):
    while True:
        try:
            message = sock.recv(1024).decode('utf-8')
            if message:
                print(f"\n{message}")
            else:
                break
        except:
            break

print(client.recv(1024).decode('utf-8'))
with client as cs:
    receiver_thread = threading.Thread(target=receive_messages, args=(cs,), daemon=True)
    receiver_thread.start()
    while True:
        send = input("Send to server: ")
        if send.lower() == 'exit':
            cs.send(bytes("exit", "utf-8"))
            break
        cs.send(bytes(send, "utf-8"))
    print(client.recv(1024).decode('utf-8'))