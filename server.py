import socket
import threading
from dotenv import load_dotenv
import os

load_dotenv()

server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
server.bind((os.environ.get('SERVER_ADDRESS', '0.0.0.0'), 3389))

server.listen(os.environ.get('MAX_CONNECTIONS', 5))
print("Server is listening...")
sockets_list = []

def threaded_client(client_socket:socket.socket, addr:str):
    global sockets_list
    with client_socket as cs:
        cs.send(bytes("Welcome to the server!", "utf-8"))
        while True:
            data = cs.recv(1024).decode('utf-8')
            if data == 'exit':
                print("Client requested to exit.")
                break
            for s in sockets_list:
                if s != cs:
                    s.send(bytes(f"Message from {addr}: {data}", "utf-8"))
        cs.send(bytes("Goodbye!", "utf-8"))
        sockets_list.remove(cs)


while True:
    client_socket, addr = server.accept()
    sockets_list.append(client_socket)
    print(f"Connection from {addr} has been established!")
    client_handler = threading.Thread(target=threaded_client, args=(client_socket, addr), daemon=True)
    client_handler.start()