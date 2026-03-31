# Backup automatico do banco e uploads.
# Execute manualmente ou agende no Agendador de Tarefas do Windows.
#
# Uso:
#     python backup.py
#
# Agendamento (Task Scheduler):
#     Programa: python
#     Argumentos: backup.py (na pasta do sistema)
#     Frequencia: Diariamente
import os
import shutil
from datetime import datetime

BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
DB_PATH     = os.path.join(BASE_DIR, 'instance', 'vendas.db')
UPLOADS_DIR = os.path.join(BASE_DIR, 'static', 'uploads')

# Pasta de destino dos backups — altere para pen drive ou pasta sincronizada (Google Drive, OneDrive, etc.)
BACKUP_ROOT = os.path.join(BASE_DIR, 'backups')

def run():
    timestamp = datetime.now().strftime('%Y-%m-%d_%H-%M')
    dest = os.path.join(BACKUP_ROOT, timestamp)
    os.makedirs(dest, exist_ok=True)

    # Backup do banco
    if os.path.exists(DB_PATH):
        shutil.copy2(DB_PATH, os.path.join(dest, 'vendas.db'))
        print(f'[OK] Banco copiado  -> {dest}/vendas.db')
    else:
        print(f'[AVISO] Banco não encontrado em: {DB_PATH}')

    # Backup dos uploads (comprovantes, arquivos de chat)
    if os.path.exists(UPLOADS_DIR):
        dest_uploads = os.path.join(dest, 'uploads')
        shutil.copytree(UPLOADS_DIR, dest_uploads)
        count = len(os.listdir(UPLOADS_DIR))
        print(f'[OK] Uploads copiados ({count} arquivos) -> {dest_uploads}')
    else:
        print(f'[AVISO] Pasta uploads não encontrada: {UPLOADS_DIR}')

    # Remove backups com mais de 30 dias
    cutoff = 30
    removed = 0
    for folder in os.listdir(BACKUP_ROOT):
        folder_path = os.path.join(BACKUP_ROOT, folder)
        if not os.path.isdir(folder_path) or folder == timestamp:
            continue
        try:
            folder_time = datetime.strptime(folder, '%Y-%m-%d_%H-%M')
            age = (datetime.now() - folder_time).days
            if age > cutoff:
                shutil.rmtree(folder_path)
                removed += 1
        except ValueError:
            pass
    if removed:
        print(f'[OK] {removed} backup(s) antigo(s) removido(s)')

    print(f'\nBackup concluído: {dest}')

if __name__ == '__main__':
    run()
