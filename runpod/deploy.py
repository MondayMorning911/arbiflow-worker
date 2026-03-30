import paramiko
import os
import sys

# --- CONFIGURATION ---
# Replace these with your Pod's details from RunPod
HOSTNAME = "77.88.99.10"  # Example IP
PORT = 12345              # Example Port
USERNAME = "root"
PRIVATE_KEY_PATH = "/Users/admin/.ssh/id_rsa"
REMOTE_DIR = "/runpod-volume/"

FILES_TO_UPLOAD = ["handler.py", "requirements.txt"]

def deploy():
    if HOSTNAME == "77.88.99.10":
        print("❌ ОШИБКА: Пожалуйста, отредактируйте deploy.py и вставьте ваш IP и ПОРТ из RunPod.")
        sys.exit(1)

    print(f"🚀 Начинаю деплой на {HOSTNAME}:{PORT}...")
    
    try:
        # Load private key
        key = paramiko.RSAKey.from_private_key_file(PRIVATE_KEY_PATH)
        
        # Connect to SSH
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(HOSTNAME, port=PORT, username=USERNAME, pkey=key)
        
        # Create SFTP client
        sftp = ssh.open_sftp()
        
        # Ensure remote directory exists
        try:
            sftp.mkdir(REMOTE_DIR)
        except IOError:
            pass # Already exists
            
        # Upload files
        for filename in FILES_TO_UPLOAD:
            local_path = os.path.join(os.path.dirname(__file__), filename)
            remote_path = os.path.join(REMOTE_DIR, filename)
            
            if os.path.exists(local_path):
                print(f"📤 Загружаю {filename}...")
                sftp.put(local_path, remote_path)
            else:
                print(f"⚠️ Файл {filename} не найден локально!")
        
        sftp.close()
        ssh.close()
        print("✅ Деплой успешно завершен!")
        print(f"📂 Файлы теперь находятся в {REMOTE_DIR} на вашем RunPod.")
        
    except Exception as e:
        print(f"❌ Произошла ошибка при деплое: {e}")

if __name__ == "__main__":
    deploy()
