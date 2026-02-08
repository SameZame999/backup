import os
import shutil
import subprocess
import argparse
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

# バックアップから除外するリスト
IGNORE_LIST: List[str] = []

logger = logging.getLogger(__name__)


def configure_logging() -> None:
    if not logger.handlers:
        logging.basicConfig(level=logging.INFO, format="%(message)s")




@dataclass
class BackupTarget:
    src: str
    dst: str
    line_no: int

def log_change(report_file: Optional[str], status: str, src: str, dst: str) -> None:
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
        logger.error("【ログ出力失敗】: %s", e)

def is_actually_mounted(path: str) -> bool:
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

def extract_backup_targets(config_path: str) -> List[BackupTarget]:
    targets = []
    with open(config_path, 'r', encoding='utf-8') as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith('#'):
                continue

            parts = line.split('\t')
            if len(parts) < 2:
                continue

            targets.append(BackupTarget(src=parts[0].strip(), dst=parts[1].strip(), line_no=line_no))
    return targets


def determine_mount_point(path: str) -> Optional[str]:
    if not path.startswith('/Volumes'):
        return None
    path_parts = [p for p in path.split(os.sep) if p]
    if len(path_parts) >= 2:
        return os.path.join(os.sep, path_parts[0], path_parts[1])
    return None


def execute_backup(entry: BackupTarget, report_file: Optional[str], today_str: str) -> bool:
    copy_success = sync_copy(entry.src, entry.dst, report_file)
    sync_success = True
    if copy_success:
        sync_success = perform_sync_deleted(entry.src, entry.dst, today_str, report_file)
    return copy_success and sync_success


def run_backup(report_file: Optional[str]) -> None:
    base_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(base_dir)
    config_path = os.path.join(base_dir, 'backup.config')

    if not os.path.exists(config_path):
        logger.error("【エラー】設定ファイルが見つかりません: %s", config_path)
        return

    today_str = datetime.now().strftime('%Y%m%d')
    logger.info("--- バックアップ開始: %s ---", datetime.now().strftime('%Y-%m-%d %H:%M:%S'))

    if report_file:
        with open(report_file, 'w', encoding='utf-8') as f:
            f.write(f"=== バックアップレポート: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===\n")

    targets = extract_backup_targets(config_path)

    valid_targets: List[BackupTarget] = []
    for entry in targets:
        dst_parent = os.path.dirname(entry.dst)
        if not dst_parent:
            logger.error("【エラー】%s行目: 出力先が不正です", entry.line_no)
            continue
        if not os.path.exists(dst_parent):
            logger.error("【エラー】親ディレクトリが存在しません: %s", dst_parent)
            continue

        mount_point = determine_mount_point(entry.dst)
        if mount_point and not is_actually_mounted(mount_point):
            logger.error("【エラー】ドライブが未マウントです: %s", mount_point)
            continue

        valid_targets.append(entry)

    for entry in valid_targets:
        success = execute_backup(entry, report_file, today_str)
        if success:
            logger.info("[SUCCESS] %s -> %s", entry.src, entry.dst)
        else:
            logger.error("[FAILED ] %s -> %s", entry.src, entry.dst)

    logger.info("--- バックアップ終了: %s ---", datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    logger.info("--- すべての処理が完了しました ---")

def sync_copy(src: str, dst: str, report_file: Optional[str]) -> bool:
    if not os.path.exists(src) and not os.path.islink(src):
        logger.error("【エラー】元パスにアクセスできません: %s", src)
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
        logger.error("【コピーエラー】%s: %s", src, e)
        return False

def safe_copy_item(src: str, dst: str, report_file: Optional[str]) -> bool:
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
        logger.error("【ファイル同期失敗】%s -> %s: %s", src, dst, e)
        return False

def perform_sync_deleted(src: str, dst: str, date_str: str, report_file: Optional[str]) -> bool:
    if not os.path.exists(dst) or not os.path.exists(src): return True
    if os.path.isdir(src) and not os.listdir(src):
        logger.error("【安全停止】元フォルダが空のため退避処理を中止しました: %s", src)
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
                    logger.error("【退避失敗】%s: %s", d_path, e)
                    overall_sync_status = False
    return overall_sync_status

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backup tool with delta logging")
    parser.add_argument("report", help="出力するレポートファイル（ログ）のパス")
    args = parser.parse_args()

    configure_logging()
    run_backup(args.report)
