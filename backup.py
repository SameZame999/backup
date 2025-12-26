import os
import shutil
import subprocess
import argparse
from datetime import datetime

# バックアップから除外するリスト
IGNORE_LIST = []

def log_change(report_file, status, src, dst):
    """
    変更履歴（ADD/UPDATE/DELETE）をファイルに追記する。
    """
    if not report_file:
        return
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    try:
        with open(report_file, 'a', encoding='utf-8') as f:
            f.write(f"[{timestamp}] {status:15}: {src} -> {dst}\n")
    except Exception as e:
        print(f"【ログ出力失敗】: {e}")

def is_actually_mounted(path):
    if not os.path.isdir(path):
        return False
    try:
        real_path = os.path.realpath(path).rstrip('/')
        if os.stat(real_path).st_dev == os.stat('/').st_dev:
            return False
        
        df_output = subprocess.check_output(['df', '-P', real_path], encoding='utf-8').splitlines()
        if len(df_output) > 1:
            actual_mount = " ".join(df_output[1].split()[5:])
            if actual_mount == '/':
                return False
        return True
    except Exception:
        return os.path.ismount(path)

def run_backup(report_file):
    base_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(base_dir)
    config_path = os.path.join(base_dir, 'backup.config')

    if not os.path.exists(config_path):
        print(f"【エラー】設定ファイルが見つかりません: {config_path}")
        return

    today_str = datetime.now().strftime('%Y%m%d')
    print(f"--- バックアップ開始: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ---")
    
    # レポートファイルの初期化（新規作成）
    if report_file:
        with open(report_file, 'w', encoding='utf-8') as f:
            f.write(f"=== バックアップレポート: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===\n")

    with open(config_path, 'r', encoding='utf-8') as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith('#'): continue

            try:
                parts = line.split('\t')
                if len(parts) < 2: continue
                src, dst = parts[0].strip(), parts[1].strip()

                dst_parent = os.path.dirname(dst)
                if not os.path.exists(dst_parent):
                    print(f"【エラー】親ディレクトリが存在しません: {dst_parent}")
                    continue

                if dst.startswith('/Volumes'):
                    path_parts = [p for p in dst.split(os.sep) if p]
                    if len(path_parts) >= 2:
                        mount_point = os.path.join(os.sep, path_parts[0], path_parts[1])
                        if not is_actually_mounted(mount_point):
                            print(f"【エラー】ドライブが未マウントです: {mount_point}")
                            continue

                # 同期・コピー処理
                copy_success = sync_copy(src, dst, report_file)

                # 退避処理
                sync_success = True
                if copy_success:
                    sync_success = perform_sync_deleted(src, dst, today_str, report_file)
                
                if copy_success and sync_success:
                    print(f"[SUCCESS] {src} -> {dst}")
                else:
                    print(f"[FAILED ] {src} -> {dst}")

            except Exception as e:
                print(f"[FAILED ] {line_no}行目: 重大な例外: {e}")

    print(f"--- バックアップ終了: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ---")
    print(f"--- すべての処理が完了しました ---")

def sync_copy(src, dst, report_file):
    if not os.path.exists(src) and not os.path.islink(src):
        print(f"【エラー】元パスにアクセスできません: {src}")
        return False
    try:
        if not os.path.isdir(src) or os.path.islink(src):
            return safe_copy_item(src, dst, report_file)
        
        overall_status = True
        for root, dirs, files in os.walk(src):
            dirs[:] = [d for d in dirs if d not in IGNORE_LIST]
            rel_path = os.path.relpath(root, src)
            dst_root = os.path.join(dst, rel_path)
            
            if not os.path.exists(dst_root):
                os.makedirs(dst_root, exist_ok=True)
            
            for name in files + dirs:
                if name in IGNORE_LIST: continue
                s_item = os.path.join(root, name)
                d_item = os.path.join(dst_root, name)
                if os.path.islink(s_item) or os.path.isfile(s_item):
                    if not safe_copy_item(s_item, d_item, report_file): 
                        overall_status = False
                elif os.path.isdir(s_item):
                    os.makedirs(d_item, exist_ok=True)
        return overall_status
    except Exception as e:
        print(f"【コピーエラー】{src}: {e}")
        return False

def safe_copy_item(src, dst, report_file):
    """
    更新日時を比較し、取得できない場合はファイルサイズを比較して
    必要に応じてコピーとログ記録を行う。
    """
    try:
        status = None
        
        # 1. ステータス判定
        if not os.path.exists(dst) and not os.path.islink(dst):
            status = "ADD"
        else:
            try:
                # 更新日時の比較 (秒単位の浮動小数点を比較)
                src_mtime = os.path.getmtime(src)
                dst_mtime = os.path.getmtime(dst)
                
                diff = src_mtime - dst_mtime
                # 1.0秒以上差があれば更新とみなす
                if diff > 1.0:
                    status = f"UPDATE(Time:{diff:.2f}s)"
            except OSError:
                # 日時が取得できない場合（権限やFSの制約）、サイズ比較を試みる
                try:
                    src_size = os.path.getsize(src)
                    dst_size = os.path.getsize(dst)
                    if src_size != dst_size:
                        status = f"UPDATE(Size:{src_size}vs{dst_size})"
                except OSError:
                    # サイズすら取得できない場合は更新しない。
                    status = None

        # 2. 処理の実行
        if status:
            if os.path.exists(dst) or os.path.islink(dst):
                if os.path.isdir(dst) and not os.path.islink(dst):
                    shutil.rmtree(dst)
                else:
                    os.remove(dst)
            
            if os.path.islink(src):
                link_to = os.readlink(src)
                os.symlink(link_to, dst)
            else:
                shutil.copy2(src, dst)
            
            log_change(report_file, status, src, dst)
        
        return True
    except Exception as e:
        print(f"【ファイル同期失敗】{src} -> {dst}: {e}")
        return False

def perform_sync_deleted(src, dst, date_str, report_file):
    if not os.path.exists(dst) or not os.path.exists(src): return True
    if os.path.isdir(src) and not os.listdir(src):
        print(f"【安全停止】元フォルダが空のため退避処理を中止しました: {src}")
        return False

    deleted_base_dir = f"{dst.rstrip(os.sep)}.deleted_at_{date_str}"
    overall_sync_status = True
    for root, dirs, files in os.walk(dst, topdown=True):
        dirs[:] = [d for d in dirs if d not in IGNORE_LIST]
        rel_path = os.path.relpath(root, dst)
        current_src_root = os.path.join(src, rel_path)
        
        for name in files + dirs:
            if name in IGNORE_LIST: continue
            s_path = os.path.join(current_src_root, name)
            d_path = os.path.join(root, name)
            
            if not os.path.exists(s_path) and not os.path.islink(s_path):
                target = os.path.join(deleted_base_dir, rel_path, name)
                try:
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    if os.path.exists(target):
                        if os.path.isdir(target) and not os.path.islink(target): shutil.rmtree(target)
                        else: os.remove(target)
                    
                    shutil.move(d_path, target)
                    log_change(report_file, "DELETE", d_path, target)
                    
                    if name in dirs: dirs.remove(name)
                except Exception as e:
                    print(f"【退避失敗】{d_path}: {e}")
                    overall_sync_status = False
    return overall_sync_status

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backup tool with delta logging")
    parser.add_argument("report", help="出力するレポートファイル（ログ）のパス")
    args = parser.parse_args()

    run_backup(args.report)
