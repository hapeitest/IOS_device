#!/usr/bin/env python3
"""
Remote Device Restore & Bootargs Automation UI (remote_restore_ui.py)

A GUI version of remote_restore.py built with PyQt6.
Requirements: pip3 install PyQt6
"""

import os
import sys
import re
import subprocess
import threading
import time
import base64
import json
from datetime import datetime

try:
    from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
                                 QLabel, QLineEdit, QPushButton, QCheckBox, QTextEdit, QComboBox,
                                 QTreeWidget, QTreeWidgetItem, QGroupBox, QMessageBox, QHeaderView, QTabWidget,
                                 QTableWidget, QTableWidgetItem, QAbstractItemView, QFileDialog)
    from PyQt6.QtCore import pyqtSignal, QObject, Qt, QThread, QSettings
    from PyQt6.QtGui import QFont, QColor
except ImportError:
    print("PyQt6 is not installed. Please run: pip3 install PyQt6")
    sys.exit(1)


class HistoryComboBox(QComboBox):
    def __init__(self, default_text="", parent=None):
        super().__init__(parent)
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        if default_text:
            self.setCurrentText(default_text)
            
    def text(self):
        return self.currentText()
        
    def setText(self, text):
        self.setCurrentText(text)
        
    def add_to_history(self):
        text = self.currentText().strip()
        if not text:
            return
        items = [self.itemText(i) for i in range(self.count())]
        if text in items:
            items.remove(text)
        items.insert(0, text)
        items = items[:5]
        self.clear()
        self.addItems(items)
        self.setCurrentText(text)

    def load_history(self, history_list):
        self.clear()
        if isinstance(history_list, str):
            if history_list:
                self.addItems([history_list])
        elif isinstance(history_list, list):
            self.addItems([str(x) for x in history_list if x])
            
    def get_history(self):
        return [self.itemText(i) for i in range(self.count())]

# --- Core Logic Functions (Adapted from CLI) ---

def run_remote_cmd(host, user, password, cmd, retries=3):
    import time
    ssh_cmd = [
        "sshpass", "-p", password,
        "ssh", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=10",
        "-o", "PubkeyAuthentication=no",
        f"{user}@{host}", cmd
    ]
    
    last_err_msg = ""
    for attempt in range(retries):
        try:
            result = subprocess.run(ssh_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
            return result.stdout.strip(), None
        except subprocess.CalledProcessError as e:
            err_msg = e.stderr.strip() or e.stdout.strip()
            last_err_msg = err_msg if err_msg else f"Command failed with exit code {e.returncode}"
            
            if attempt < retries - 1:
                time.sleep(1.5)  # Short delay before retry
                continue
            return None, last_err_msg
        except FileNotFoundError:
            return None, "sshpass is not installed on this machine. Run 'brew install hudochenkov/sshpass/sshpass' or 'brew install sshpass'."
            
    return None, f"Failed after {retries} attempts. Last error: {last_err_msg}"

def parse_tactl_output(output):
    devices = []
    if not output:
        return devices
    lines = output.splitlines()
    if len(lines) < 3:
        return devices

    content_lines = []
    divider_found = False
    for line in lines:
        if "----------------" in line:
            divider_found = True
            continue
        if divider_found and line.strip():
            content_lines.append(line)

    for line in content_lines:
        parts = line.split()
        if len(parts) < 5:
            continue
        
        if "CAMEmbeddedDeviceResource" in line and "Attached" in line:
            primate_match = re.search(r'(ChimpSWD-[0-9A-Fa-f]+|KanziSWD-[0-9A-Fa-f]+)', line)
            ecid_match = re.findall(r'\b\d{15,20}\b', line)
            model_match = re.search(r'\bv\d+ap\b|\bd\d+ap\b', line)
            
            if primate_match and ecid_match:
                ecid = ecid_match[-1]
                primate_path = primate_match.group(1)
                model = model_match.group(0) if model_match else "Unknown"
                
                serial = "Unknown"
                serial_candidates = re.findall(r'\b[A-Z0-9]{10,12}\b', line)
                for cand in serial_candidates:
                    if cand != ecid and "Chimp" not in cand and "Kanzi" not in cand:
                        serial = cand
                        break
                
                devices.append({
                    "ecid": ecid,
                    "primatePath": primate_path,
                    "model": model,
                    "serial": serial
                })
    return devices

def copy_file_to_remote(host, user, password, local_path, remote_path):
    scp_cmd = [
        "sshpass", "-p", password,
        "scp", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=10",
        local_path, f"{user}@{host}:{remote_path}"
    ]
    try:
        subprocess.run(scp_cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return True, None
    except subprocess.CalledProcessError as e:
        return False, e.stderr.strip() or e.stdout.strip()

def launch_uart_monitor_terminal(host, user, password, chimp_id, prefix, save_path=None, logger=None, email="", file_radar=False, comp_name="", comp_version="", keep_panic=False, ignore_panic=False, is_brick_powered=False, retries=3):
    dev_path = f"/dev/cu.chimp-{chimp_id}-ch-0"
    local_script_dir = os.path.dirname(os.path.abspath(__file__))
    local_uart_monitor = os.path.join(local_script_dir, "uart_monitor.py")
    remote_uart_monitor = "~/Desktop/Test_Automator/uart_monitor.py"
    local_file_radar = os.path.join(local_script_dir, "file_unit_radar.py")
    remote_file_radar = "~/Desktop/Test_Automator/file_unit_radar.py"
    
    save_path_arg = f" --save-path '{save_path}'" if save_path else ""
    email_arg = f" --email '{email}'" if email else ""
    file_radar_arg = " --file-radar" if file_radar else ""
    comp_name_arg = f" --component-name '{comp_name}'" if comp_name else ""
    comp_version_arg = f" --component-version '{comp_version}'" if comp_version else ""
    keep_panic_arg = " --keep-panic" if keep_panic else ""
    ignore_panic_arg = " --ignore-panic" if ignore_panic else ""
    is_brick_arg = " --is-brick" if is_brick_powered else ""
    
    applescript = f'''
    tell application "Terminal"
        do script "python3 ~/Desktop/Test_Automator/uart_monitor.py {dev_path} --baud 115200{save_path_arg}{email_arg}{file_radar_arg}{comp_name_arg}{comp_version_arg}{keep_panic_arg}{ignore_panic_arg}{is_brick_arg}"
        activate
    end tell
    '''
    
    b64_script = base64.b64encode(applescript.encode('utf-8')).decode('utf-8')
    remote_cmd = f"echo {b64_script} | base64 --decode | osascript"
    ssh_cmd = ["sshpass", "-p", password, "ssh", "-o", "StrictHostKeyChecking=no", "-o", "PubkeyAuthentication=no", f"{user}@{host}", remote_cmd]
    
    for attempt in range(retries):
        if logger: logger.log(f"Attempt {attempt + 1} of {retries}: Uploading local uart_monitor.py to remote host...", prefix, "info")
        
        # Ensure the target directory exists
        run_remote_cmd(host, user, password, "mkdir -p ~/Desktop/Test_Automator")
        
        success, err = copy_file_to_remote(host, user, password, local_uart_monitor, remote_uart_monitor)
        
        # Also sync file_unit_radar.py if radar filing is enabled
        if file_radar:
            copy_file_to_remote(host, user, password, local_file_radar, remote_file_radar)
            
        if not success:
            if logger: logger.log(f"Failed to copy uart_monitor.py: {err}", prefix, "warning")
            time.sleep(1.5)
            continue
            
        pip_cmd = "python3 -c 'import serial' 2>/dev/null || python3 -m pip install pyserial --user --quiet || pip3 install pyserial --quiet"
        run_remote_cmd(host, user, password, pip_cmd)
        
        time.sleep(1) # Add 1 second delay before spawning Terminal
        
        try:
            subprocess.run(ssh_cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if logger: logger.log(f"Spawned remote Terminal window for uart_monitor.py on {dev_path}", prefix, "success")
            return True
        except subprocess.CalledProcessError as e:
            if logger: logger.log(f"Could not spawn Terminal: {e}", prefix, "warning")
            time.sleep(1.5)
            continue
            
    if logger: logger.log(f"Failed to launch uart_monitor after {retries} attempts. Falling back to nanokdp...", prefix, "warning")
    return launch_nanokdp_terminal_fallback(host, user, password, chimp_id, prefix, logger)

def launch_nanokdp_terminal_fallback(host, user, password, chimp_id, prefix, logger=None):
    dev_path = f"/dev/cu.chimp-{chimp_id}-ch-0"
    applescript = f'''
    tell application "Terminal"
        do script "/usr/local/bin/nanokdp --timestamp='%F %T.sss' -l -d {dev_path}"
        activate
    end tell
    '''
    b64_script = base64.b64encode(applescript.encode('utf-8')).decode('utf-8')
    remote_cmd = f"echo {b64_script} | base64 --decode | osascript"
    ssh_cmd = ["sshpass", "-p", password, "ssh", "-o", "StrictHostKeyChecking=no", f"{user}@{host}", remote_cmd]
    try:
        subprocess.run(ssh_cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if logger: logger.log(f"Spawned remote Terminal window for nanokdp on {dev_path}", prefix, "success")
        return True
    except subprocess.CalledProcessError as e:
        if logger: logger.log(f"Could not spawn Terminal (falling back to background log): {e}", prefix, "warning")
        run_remote_cmd(host, user, password, "mkdir -p ~/Desktop/Test_Automator")
        nanokdp_log_file = f"~/Desktop/Test_Automator/nanokdp_{chimp_id}.log"
        nanokdp_cmd = f"nohup /usr/local/bin/nanokdp --timestamp='%F %T.sss' -l -d {dev_path} > {nanokdp_log_file} 2>&1 &"
        run_remote_cmd(host, user, password, nanokdp_cmd)
        return False

def run_serial_login_automation(host, user, password, prefix, chimp_id, primate_path, sequence_name, is_brick_powered, save_path, extra_serial_cmds, logger, do_restore=True, email="", file_radar=False, comp_name="", comp_version=""):
    if extra_serial_cmds is None:
        extra_serial_cmds = []
    dev_path = f"/dev/cu.chimp-{chimp_id}-ch-0"
    
    launch_uart_monitor_terminal(host, user, password, chimp_id, prefix, save_path, logger, email, file_radar, comp_name, comp_version, keep_panic=False, ignore_panic=False, is_brick_powered=is_brick_powered)
    
    if do_restore:
        boot_wait_time = 180
        logger.log(f"Waiting {boot_wait_time}s (3 minutes) for device to complete restore bootup...", prefix, "info")
    else:
        boot_wait_time = 30
        logger.log(f"Waiting {boot_wait_time}s (30 seconds). No restore booting is needed...", prefix, "info")
    time.sleep(boot_wait_time)

    logger.log(f"Executing sequential serial shell login and setting sequence: '{sequence_name}'", prefix, "info")
    
    serial_steps = [
    	(f'echo -e "\\n" > {dev_path}', 2, "Sending carriage return"),
    	(f'echo -e "\\n" > {dev_path}', 2, "Sending carriage return"),
        (f'echo -e "root\\n" > {dev_path}', 2, "Entering root user"),
        (f'echo -e "\\n" > {dev_path}', 2, "Sending carriage return"),
        (f'echo -e "alpine\\n" > {dev_path}', 3, "Sending credentials password"),
    ]
    for cmd in extra_serial_cmds:
        safe_cmd = cmd.replace('"', '\\"')
        serial_steps.append((f'echo -e "{safe_cmd} \\n" > {dev_path}', 3, f"Extra command: {cmd}"))

    # Execute base shell login + extra commands first
    for cmd, delay, desc in serial_steps:
        logger.log(f"{desc}...", prefix, "info")
        _, err = run_remote_cmd(host, user, password, cmd)
        if err:
            logger.log(f"Serial Step Failed: {err}", prefix, "warning")
        time.sleep(delay)

    # Bootargs configuration with up to 3 retries verification loop
    max_retries = 3
    bootargs_success = False
    
    log_file1 = f"{save_path.rstrip('/')}/serial_log/cu.chimp-{chimp_id}-ch-0_serial.log" if save_path else ""

    for attempt in range(1, max_retries + 1):
        logger.log(f"Attempt {attempt}/{max_retries}: Configuring bootargs sequence -> {sequence_name}", prefix, "info")
        
        setup_cmds = [
            (f'echo -e "diagstool bootargs -a astro={sequence_name} \n" > {dev_path}', 3),
            (f'echo -e "\n" > {dev_path}', 2),
            (f'echo -e "diagstool bootargs -p \n" > {dev_path}', 3),
            (f'echo -e "\n" > {dev_path}', 2),
        ]
        
        for cmd, delay in setup_cmds:
            _, err = run_remote_cmd(host, user, password, cmd)
            if err:
                logger.log(f"Failed sending serial command during bootargs config: {err}", prefix, "warning")
            time.sleep(delay)
            
        logger.log(f"Verifying 'astro={sequence_name}' in bootargs output...", prefix, "info")
        verify_cmd = f"tail -n 100 {log_file1} 2>/dev/null | grep -a -i 'astro={sequence_name}'"
        
        # Poll a few times to allow log buffers to flush
        for _ in range(4):
            stdout, err = run_remote_cmd(host, user, password, verify_cmd)
            # Strip potential ANSI codes from stdout
            clean_stdout = re.sub(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])', '', stdout) if stdout else ""
            if clean_stdout and f"astro={sequence_name}".lower() in clean_stdout.lower():
                bootargs_success = True
                break
            time.sleep(2)
        
        if bootargs_success:
            logger.log(f"Bootargs verified successfully on attempt {attempt}! (astro={sequence_name} is set)", prefix, "success")
            break
        else:
            logger.log(f"Verification failed on attempt {attempt}. 'astro={sequence_name}' not found.", prefix, "warning")

    if not bootargs_success:
        logger.log(f"Bootargs verification failed after {max_retries} attempts! Proceeding with reboot anyway just in case...", prefix, "warning")

    logger.log("Triggering device reboot...", prefix, "info")
    _, err = run_remote_cmd(host, user, password, f'echo -e "reboot\\n" > {dev_path}')
    if err:
        logger.log(f"Reboot command failed: {err}", prefix, "warning")
    time.sleep(3)

    if is_brick_powered:
        logger.log("Executing relay ignoreacc override...", prefix, "info")
        ignoreacc_cmd = f"/usr/local/bin/astrisctl --probe {primate_path} relay ignoreacc 0"
        _, err = run_remote_cmd(host, user, password, ignoreacc_cmd)
        if err:
            logger.log(f"Power Brick Override Failed: {err}", prefix, "warning")
        else:
            logger.log(f"Power Brick override executed successfully!", prefix, "success")

    logger.log("Serial login sequence execution completed successfully!", prefix, "success")


def run_camtool_automation(host, user, password, prefix, chimp_id, primate_path, sequence_name, is_brick_powered, save_path, extra_serial_cmds, logger, do_restore=True, email="", file_radar=False, comp_name="", comp_version="", keep_panic=False, ignore_panic=False):
    if extra_serial_cmds is None:
        extra_serial_cmds = []
    dev_path = f"/dev/cu.chimp-{chimp_id}-ch-0"
    
    launch_uart_monitor_terminal(host, user, password, chimp_id, prefix, save_path, logger, email, file_radar, comp_name, comp_version, keep_panic, ignore_panic, is_brick_powered=is_brick_powered)
    
    if do_restore:
        boot_wait_time = 180
        logger.log(f"Waiting {boot_wait_time}s (3 minutes) for device to complete restore bootup...", prefix, "info")
    else:
        boot_wait_time = 30
        logger.log(f"Waiting {boot_wait_time}s (30 seconds). No restore booting is needed...", prefix, "info")
    time.sleep(boot_wait_time)

    logger.log(f"Fetching precise Serial Number for {primate_path} via camtool...", prefix, "info")
    sn_cmd = f"/usr/local/bin/camtool -p {primate_path} serial_number"
    sn_out, _ = run_remote_cmd(host, user, password, sn_cmd)
    if sn_out:
        actual_serial = sn_out.strip().split()[-1]
        if actual_serial and actual_serial.isalnum():
            logger.log(f"Confirmed actual Serial Number: {actual_serial}", prefix, "success")
            resolved_save_path = save_path.replace('~', '$HOME')
            sed_cmd = f"sed -i '' -E 's/Serial: [^,]*, PrimatePath: {primate_path}/Serial: {actual_serial}, PrimatePath: {primate_path}/g' {resolved_save_path.rstrip('/')}/session_summary_*.txt 2>/dev/null"
            run_remote_cmd(host, user, password, sed_cmd)

    logger.log(f"Executing camtool automation and setting sequence: '{sequence_name}'", prefix, "info")
    
    for cmd in extra_serial_cmds:
        safe_cmd = cmd.replace("'", "'\\''")
        camtool_cmd = f"/usr/local/bin/camtool -p {primate_path} run_command '{safe_cmd}'"
        logger.log(f"Extra command via camtool: {cmd}...", prefix, "info")
        _, err = run_remote_cmd(host, user, password, camtool_cmd)
        if err:
            logger.log(f"Camtool Extra Command Failed: {err}", prefix, "warning")
        time.sleep(3)

    max_retries = 3
    bootargs_success = False

    for attempt in range(1, max_retries + 1):
        logger.log(f"Attempt {attempt}/{max_retries}: Configuring bootargs sequence -> {sequence_name} via camtool", prefix, "info")
        
        set_bootargs_cmd = f"/usr/local/bin/camtool -p {primate_path} run_command 'diagstool bootargs -a astro={sequence_name}'"
        _, err = run_remote_cmd(host, user, password, set_bootargs_cmd)
        if err:
            logger.log(f"Failed setting bootargs via camtool: {err}", prefix, "warning")
        time.sleep(3)
        
        logger.log(f"Verifying 'astro={sequence_name}' in bootargs output via camtool...", prefix, "info")
        check_bootargs_cmd = f"/usr/local/bin/camtool -p {primate_path} run_command 'diagstool bootargs -p'"
        
        stdout, err = run_remote_cmd(host, user, password, check_bootargs_cmd)
        clean_stdout = re.sub(r'\x1B(?:[@-Z\\-_]|\\[[0-?]*[ -/]*[@-~])', '', stdout) if stdout else ""
        
        if clean_stdout and f"astro={sequence_name}".lower() in clean_stdout.lower():
            bootargs_success = True
            logger.log(f"Bootargs verified successfully on attempt {attempt}! (astro={sequence_name} is set)", prefix, "success")
            break
        else:
            logger.log(f"Verification failed on attempt {attempt}. 'astro={sequence_name}' not found.", prefix, "warning")
            time.sleep(2)

    if not bootargs_success:
        logger.log(f"Bootargs verification failed after {max_retries} attempts! Proceeding with reboot anyway just in case...", prefix, "warning")

    logger.log(f"Validating astro sequence '{sequence_name}'...", prefix, "info")
    validate_cmd = f"/usr/local/bin/camtool -p {primate_path} run_command 'astro viz {sequence_name}'"
    stdout, err = run_remote_cmd(host, user, password, validate_cmd)
    
    clean_output = ""
    if stdout:
        clean_output += re.sub(r'\x1B(?:[@-Z\\-_]|\\[[0-?]*[ -/]*[@-~])', '', stdout)
    if err:
        clean_output += "\n" + err
        
    error_detected = False
    error_msg = ""
    
    # Only take this as an error if output specifically says "failed to load flow"
    if "failed to load flow" in clean_output.lower():
        error_detected = True
        error_msg = clean_output

    if error_detected:
        logger.log(f"Sequence validation failed: {error_msg}. Aborting operation and closing monitor session.", prefix, "error")
        
        # Close the uart_monitor or nanokdp session on the remote host
        kill_cmd = f"pkill -f '{dev_path}'"
        run_remote_cmd(host, user, password, kill_cmd)
        logger.log("Remote monitor session terminated.", prefix, "info")

        if hasattr(logger, 'popup'):
            logger.popup("Sequence Validation Error", f"Error validating sequence '{sequence_name}' on {primate_path}:\n\n{error_msg}\n\nOperation aborted and monitor session closed. Please double check the sequence.")
        return  # Halt the rest of the operation
    else:
        logger.log(f"Sequence '{sequence_name}' validated successfully.", prefix, "success")

    logger.log("Triggering device reboot via camtool...", prefix, "info")
    reboot_cmd = f"/usr/local/bin/camtool -p {primate_path} run_command 'reboot'"
    _, err = run_remote_cmd(host, user, password, reboot_cmd)
    if err:
        logger.log(f"Reboot command failed: {err}", prefix, "warning")
    time.sleep(3)

    if is_brick_powered:
        logger.log("Executing relay ignoreacc override...", prefix, "info")
        ignoreacc_cmd = f"/usr/local/bin/astrisctl --probe {primate_path} relay ignoreacc 0"
        _, err = run_remote_cmd(host, user, password, ignoreacc_cmd)
        if err:
            logger.log(f"Power Brick Override Failed: {err}", prefix, "warning")
        else:
            logger.log(f"Power Brick override executed successfully!", prefix, "success")

    logger.log("Camtool automation sequence execution completed successfully!", prefix, "success")

def restore_thread_worker(host, user, password, dev, recipe_path, sequence_name, is_brick_powered, save_path, extra_serial_cmds, logger, do_restore=True, restore_only=False, email="", file_radar=False, comp_name="", comp_version="", keep_panic=False, ignore_panic=False):
    ecid = dev['ecid']
    primate_path = dev['primatePath']
    prefix = f"ECID:{ecid[-6:]}"
    
    chimp_match = re.search(r'(?:ChimpSWD|KanziSWD)-([0-9A-Fa-f]+)', primate_path)
    chimp_id = chimp_match.group(1) if chimp_match else None

    restore_success = False
    
    if do_restore:
        logger.log(f"Putting device {primate_path} in DFU mode...", prefix, "info")
        
        dfu_cmd = f"/usr/local/bin/astrisctl --probe {primate_path} dfu"
        stdout, err = run_remote_cmd(host, user, password, dfu_cmd)
        if err:
            logger.log(f"DFU Command Failed (proceeding anyway): {err}", prefix, "warning")
        else:
            logger.log(f"Successfully sent to DFU.", prefix, "success")
            time.sleep(3)
        
        logger.log(f"Starting mobile_restore with recipe: {os.path.basename(recipe_path)}...", prefix, "info")
        
        # Expand tilde (~) to absolute path for the remote host to avoid shell quoting errors
        if recipe_path.startswith("~"):
            remote_home = "/var/root" if user == "root" else f"/Users/{user}"
            recipe_path = recipe_path.replace("~", remote_home, 1)

        # Sync restore_unit.py to remote
        local_script_dir = os.path.dirname(os.path.abspath(__file__))
        local_restore_unit = os.path.join(local_script_dir, "restore_unit.py")
        remote_restore_unit = "/tmp/restore_unit.py"
        remote_status_file = f"/tmp/restore_status_{ecid}.json"
        
        logger.log("Copying restore script to remote host...", prefix, "info")
        copy_success, copy_err = copy_file_to_remote(host, user, password, local_restore_unit, remote_restore_unit)
        if not copy_success:
            logger.log(f"Failed to copy restore_unit.py: {copy_err}", prefix, "error")
            return
            
        # Clean up old status file
        run_remote_cmd(host, user, password, f"rm -f {remote_status_file}")

        # Execute it detached
        restore_cmd = f"nohup python3 {remote_restore_unit} {ecid} {primate_path} {recipe_path} {remote_status_file} > /tmp/restore_unit_{ecid}.log 2>&1 &"
        ssh_restore_cmd = ["sshpass", "-p", password, "ssh", "-o", "StrictHostKeyChecking=no", f"{user}@{host}", restore_cmd]
        
        try:
            subprocess.run(ssh_restore_cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            logger.log("Remote restore process started successfully (detached).", prefix, "success")
            
            # Polling status file
            logger.log("Polling remote status...", prefix, "info")
            import json
            last_progress = ""
            
            while True:
                time.sleep(5)
                cat_cmd = ["sshpass", "-p", password, "ssh", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=5", f"{user}@{host}", f"cat {remote_status_file}"]
                cat_result = subprocess.run(cat_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                
                if cat_result.returncode != 0:
                    # Could be network drop or file doesn't exist yet
                    continue
                
                try:
                    data = json.loads(cat_result.stdout.strip())
                    status = data.get("status", "UNKNOWN")
                    progress = data.get("progress", "")
                    
                    if progress and progress != last_progress:
                        logger.log(f"[{status}] {progress}", prefix, "info")
                        last_progress = progress
                        
                    if status == "COMPLETED":
                        logger.log("RESTORE COMPLETED SUCCESSFULLY!", prefix, "success")
                        restore_success = True
                        break
                    elif status == "FAILED":
                        logger.log(f"RESTORE FAILED!", prefix, "error")
                        logger.log("--- Last log lines ---", prefix, "error")
                        for line in data.get("log", []):
                            logger.log(line, prefix, "error")
                        logger.log("----------------------", prefix, "error")
                        break
                        
                except json.JSONDecodeError:
                    # Incomplete write, skip this poll
                    continue

        except Exception as e:
            logger.log(f"Exception during restore polling: {str(e)}", prefix, "error")
    else:
        restore_success = True
        logger.log("Skipping restore phase as requested.", prefix, "success")

    if restore_success and chimp_id and not restore_only:
        try:
            run_camtool_automation(host, user, password, prefix, chimp_id, primate_path, sequence_name, is_brick_powered, save_path, extra_serial_cmds, logger, do_restore, email, file_radar, comp_name, comp_version, keep_panic, ignore_panic)
        except Exception as e:
            logger.log(f"Post-Restore Config Error: {str(e)}", prefix, "error")
    elif restore_success and not chimp_id:
        logger.log("Restore succeeded but no chimp_id. Post-boot automation skipped.", prefix, "warning")
# --- Qt Threading Classes ---

class LoggerSignal(QObject):
    new_log = pyqtSignal(str, str, str)
    show_popup = pyqtSignal(str, str)
    def log(self, msg, prefix="INFO", level="info"):
        self.new_log.emit(msg, prefix, level)
    def popup(self, title, msg):
        self.show_popup.emit(title, msg)

class FetchDevicesThread(QThread):
    finished = pyqtSignal(list, str) # devices_list, error_message
    
    def __init__(self, host, user, password):
        super().__init__()
        self.host = host
        self.user = user
        self.password = password
        
    def run(self):
        tactl_cmd = "/usr/local/bin/tactl resources list --properties hardwareModel serialNumber locationID primatePath ecid"
        stdout, err = run_remote_cmd(self.host, self.user, self.password, tactl_cmd)
        
        if err or stdout is None:
            self.finished.emit([], err if err else "Unknown error")
        else:
            devices = parse_tactl_output(stdout)
            self.finished.emit(devices, "")

class RestoreWorkerThread(QThread):
    finished = pyqtSignal()
    
    def __init__(self, host, user, password, selected_devices, radars, sequence, base_recipe, bundle_id, save_path, is_brick, extra_cmds, logger, do_restore=True, restore_only=False, email="", file_radar=False, comp_name="", comp_version="", keep_panic=False, ignore_panic=False, use_prkit=False, device_type="", prkit_id="", atp_kit_id="", upload_radar="", generate_issuebot=False, milestone="", events="", wait_for_bundle=False): 
        super().__init__()
        self.upload_radar = upload_radar
        self.use_prkit = use_prkit
        self.device_type = device_type
        self.prkit_id = prkit_id
        self.atp_kit_id = atp_kit_id
        self.host = host
        self.user = user
        self.password = password
        self.selected_devices = selected_devices
        self.radars = radars
        self.sequence = sequence
        self.base_recipe = base_recipe
        self.bundle_id = bundle_id
        self.save_path = save_path
        self.is_brick = is_brick
        self.extra_cmds = extra_cmds
        self.logger = logger
        self.do_restore = do_restore
        self.restore_only = restore_only
        self.email = email
        self.file_radar = file_radar
        self.comp_name = comp_name
        self.comp_version = comp_version
        self.keep_panic = keep_panic
        self.ignore_panic = ignore_panic
        self.generate_issuebot = generate_issuebot
        self.milestone = milestone
        self.events = events
        self.wait_for_bundle = wait_for_bundle
        
    def run(self):
        # 0. Check if the log save path exists on the remote host, if so, append timestamp
        if self.save_path:
            resolved_save_path = self.save_path.replace('~', '$HOME')
            check_cmd = f'[ -d "{resolved_save_path}" ] && echo "exists" || echo "not_found"'
            check_out, check_err = run_remote_cmd(self.host, self.user, self.password, check_cmd)
            
            if check_out and check_out.strip() == "exists":
                timestamp = datetime.now().strftime("%m-%d-%H-%M-%S")
                self.save_path = f"{self.save_path.rstrip('/')}_{timestamp}"
                self.logger.log(f"Save path already exists. Using new session path: {self.save_path}", "SYSTEM", "info")

        radar_ids = [r.strip() for r in re.split(r'[\s,]+', self.radars) if r.strip()] if self.radars else []
        
        radar_info = ', '.join(radar_ids) if radar_ids else 'N/A'
        bundle_info = self.bundle_id or 'N/A'
        
        if not self.do_restore:
            radar_info = 'No Applicable for this session'
            bundle_info = 'No Applicable for this session'
            
        # 1. Summary
        if getattr(self, 'use_prkit', False):
            prkit_info = f"BATS Kit ID: {self.prkit_id}" if self.prkit_id else f"ATP Kit ID: {self.atp_kit_id}"
            prkit_display = f"Yes ({prkit_info}, Device Type: {self.device_type})"
        else:
            prkit_display = "No"

        summary_lines = [
            "=== Restore Session Summary ===",
            f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"Restore Enabled: {'Yes' if self.do_restore else 'No'}",
            f"Radars: {radar_info}",
            f"Sequence: {self.sequence}",
            f"Bundle ID: {bundle_info}",
            f"PRKit: {prkit_display}",
            "Selected Units:"
        ]
        for dev in self.selected_devices:
            summary_lines.append(f"  - Serial: {dev['serial']}, PrimatePath: {dev['primatePath']}")
            
        summary_text = "\n".join(summary_lines)
        summary_filename = f"session_summary_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        resolved_save_path = self.save_path.replace('~', '$HOME')
        remote_summary_path = f"{resolved_save_path.rstrip('/')}/{summary_filename}"
        
        b64_summary = base64.b64encode(summary_text.encode('utf-8')).decode('utf-8')
        mkdir_cmd = f'mkdir -p "{resolved_save_path}" && echo {b64_summary} | base64 --decode > "{remote_summary_path}"'
        run_remote_cmd(self.host, self.user, self.password, mkdir_cmd)
        self.logger.log(f"Saved session summary to {self.save_path}", "SUMMARY", "info")

        # 3. Dispatch to Remote session_coordinator.py
        self.logger.log("Dispatching entire session to remote coordinator...", "SYSTEM", "info")
        
        # Build devices list
        devices_list = []
        for dev in self.selected_devices:
            devices_list.append({
                "ecid": dev['ecid'],
                "primatePath": dev['primatePath'],
                "sequence_name": self.sequence,
                "is_brick_powered": self.is_brick,
                "model": dev.get('hardwareModel', 'Unknown'),
                "serial": dev.get('serialNumber', 'Unknown')
            })

        local_script_dir = os.path.dirname(os.path.abspath(__file__))
        local_coord = os.path.join(local_script_dir, "session_coordinator.py")
        remote_coord = "~/Desktop/Test_Automator/session_coordinator.py"
        
        # Copy scripts to remote host
        run_remote_cmd(self.host, self.user, self.password, "mkdir -p ~/Desktop/Test_Automator")
        copy_file_to_remote(self.host, self.user, self.password, local_coord, remote_coord)
        
        for script_name in ["uart_monitor.py", "file_unit_radar.py", "report_generation.py", "radar_download.py"]:
            local_path = os.path.join(local_script_dir, script_name)
            remote_path = f"~/Desktop/Test_Automator/{script_name}"
            if os.path.exists(local_path):
                copy_file_to_remote(self.host, self.user, self.password, local_path, remote_path)
        
        resolved_save_path = self.save_path.replace('~', '$HOME')
        
        session_info_dict = {
            "do_restore": self.do_restore,
            "restore_only": self.restore_only,
            "use_prkit": getattr(self, 'use_prkit', False),
            "prkit_id": getattr(self, 'prkit_id', None),
            "atp_kit_id": getattr(self, 'atp_kit_id', None),
            "device_type": getattr(self, 'device_type', None),
            "base_recipe": getattr(self, 'base_recipe', None),
            "radars": getattr(self, 'radars', None),
            "bundle_id": getattr(self, 'bundle_id', None),
            "wait_for_bundle": getattr(self, 'wait_for_bundle', False),
            "extra_cmds": self.extra_cmds,
            "file_radar": self.file_radar,
            "generate_issuebot": getattr(self, 'generate_issuebot', False),
            "milestone": getattr(self, 'milestone', ""),
            "events": getattr(self, 'events', ""),
            "upload_radar": getattr(self, 'upload_radar', ""),
            "comp_name": self.comp_name,
            "comp_version": self.comp_version,
            "keep_panic": self.keep_panic,
            "ignore_panic": self.ignore_panic,
            "devices": devices_list
        }
        
        session_info_b64 = base64.b64encode(json.dumps(session_info_dict).encode('utf-8')).decode('utf-8')
        b64_flag = f" --session-info-b64 '{session_info_b64}'"
        
        # We run the coordinator even if email is empty (so it can manage the restores/camtool!)
        email_flag = f" --email '{self.email}'" if self.email else " --email 'NONE'"
        radar_flag = " --file-radar" if self.file_radar else ""
        
        coord_timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        coord_log = f"~/Desktop/Test_Automator/coordinator_{coord_timestamp}.log"
        
        coord_cmd = f"bash -l -c \"nohup python3 -u ~/Desktop/Test_Automator/session_coordinator.py --save-path '{resolved_save_path}'{email_flag}{radar_flag}{b64_flag} > {coord_log} 2>&1 &\""                
        
        run_remote_cmd(self.host, self.user, self.password, coord_cmd)
        self.logger.log("Remote session coordinator launched successfully. All tasks are now executing on the remote host.", "SYSTEM", "success")
        
        self.logger.log("ALL TASKS DISPATCHED.", "SYSTEM", "success")
        self.finished.emit()

# --- PyQt6 Main Window ---

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Remote Device Restore & Bootargs Automator")
        self.resize(750, 700)
        
        self.devices = []
        self.active_threads = set() # Store running threads to prevent garbage collection
        self.last_launched_params = None # Store parameters from the last launch to prevent duplicates
        self.task_counter = 0 # Track number of tasks launched
        self.logger_signal = LoggerSignal()
        self.logger_signal.new_log.connect(self.append_log)
        self.logger_signal.show_popup.connect(self.show_popup_msg)
        
        # Initialize settings instance
        self.settings = QSettings("Apple", "RemoteRestoreUI")
        
        self.setup_ui()
        self.load_settings()
        self.append_log("UI Initialized. Enter Connection Settings and click 'Fetch Devices'.", "SYSTEM", "info")
        
    def load_settings(self):
        
        # Load history first
        for field, key in [
            (self.host_input, "host"),
            (self.user_input, "user"),
            (self.radars_input, "radars"),
            (self.sequence_input, "sequence"),
            (self.base_recipe_input, "base_recipe"),
            (self.bundle_input, "bundle_id"),
            (self.save_path_input, "save_path"),
            (self.email_input, "email"),
            (self.comp_name_input, "comp_name"),
            (self.comp_version_input, "comp_version"),
            (self.device_type_input, "device_type"),
            (self.prkit_id_input, "prkit_id"),
            (self.atp_kit_id_input, "atp_kit_id"),
            (self.upload_radar_input, "upload_radar"),
            (self.milestone_input, "milestone"),
            (self.events_input, "events")
        ]:
            field.load_history(self.settings.value(f"{key}_history", []))

        # Load string inputs (fallback to default values if not found)
        self.host_input.setText(self.settings.value("host", ""))
        self.user_input.setText(self.settings.value("user", "gdqe"))
        self.radars_input.setText(self.settings.value("radars", ""))
        self.sequence_input.setText(self.settings.value("sequence", ""))
        self.base_recipe_input.setText(self.settings.value("base_recipe", "~/Desktop/V68_SW-DOWNLOAD-0222.pr"))
        self.bundle_input.setText(self.settings.value("bundle_id", ""))
        self.save_path_input.setText(self.settings.value("save_path", ""))
        self.email_input.setText(self.settings.value("email", ""))
        self.extra_cmds_input.setPlainText(self.settings.value("extra_cmds", ""))
        self.comp_name_input.setText(self.settings.value("comp_name", "iOS FBR SQA"))
        self.comp_version_input.setText(self.settings.value("comp_version", "Burnin"))
        self.milestone_input.setText(self.settings.value("milestone", ""))
        self.events_input.setText(self.settings.value("events", ""))
        self.upload_radar_input.setText(self.settings.value("upload_radar", ""))
        
        self.device_type_input.setText(self.settings.value("device_type", ""))
        self.prkit_id_input.setText(self.settings.value("prkit_id", ""))
        self.atp_kit_id_input.setText(self.settings.value("atp_kit_id", ""))
        
        # Load checkbox states
        prkit_cb_val = self.settings.value("prkit_cb", False, type=bool)
        self.prkit_cb.setChecked(prkit_cb_val)
        self.toggle_prkit_fields(prkit_cb_val)
        
        restore_cb_val = self.settings.value("restore_cb", True, type=bool)
        self.restore_cb.setChecked(restore_cb_val)
        self.toggle_restore_fields(restore_cb_val)
        
        self.brick_cb.setChecked(self.settings.value("brick_cb", False, type=bool))
        self.file_radar_cb.setChecked(self.settings.value("file_radar_cb", False, type=bool))
        self.generate_issuebot_report_cb.setChecked(self.settings.value("generate_issuebot_report_cb", False, type=bool))
        self.keep_panic_cb.setChecked(self.settings.value("keep_panic_cb", False, type=bool))
        self.ignore_panic_cb.setChecked(self.settings.value("ignore_panic_cb", False, type=bool))
        self.wait_for_bundle_cb.setChecked(self.settings.value("wait_for_bundle_cb", False, type=bool))
        self.toggle_file_radar_fields()

    def closeEvent(self, event):
        # Save all current input values when the window closes

        # Save histories
        for field, key in [
            (self.host_input, "host"),
            (self.user_input, "user"),
            (self.radars_input, "radars"),
            (self.sequence_input, "sequence"),
            (self.base_recipe_input, "base_recipe"),
            (self.bundle_input, "bundle_id"),
            (self.save_path_input, "save_path"),
            (self.email_input, "email"),
            (self.comp_name_input, "comp_name"),
            (self.comp_version_input, "comp_version"),
            (self.device_type_input, "device_type"),
            (self.prkit_id_input, "prkit_id"),
            (self.atp_kit_id_input, "atp_kit_id"),
            (self.upload_radar_input, "upload_radar"),
            (self.milestone_input, "milestone"),
            (self.events_input, "events")
        ]:
            field.add_to_history()
            self.settings.setValue(f"{key}_history", field.get_history())

        self.settings.setValue("host", self.host_input.text().strip())
        self.settings.setValue("user", self.user_input.text().strip())
        self.settings.setValue("radars", self.radars_input.text().strip())
        self.settings.setValue("sequence", self.sequence_input.text().strip())
        self.settings.setValue("base_recipe", self.base_recipe_input.text().strip())
        self.settings.setValue("bundle_id", self.bundle_input.text().strip())
        self.settings.setValue("save_path", self.save_path_input.text().strip())
        self.settings.setValue("email", self.email_input.text().strip())
        self.settings.setValue("extra_cmds", self.extra_cmds_input.toPlainText().strip())
        self.settings.setValue("restore_cb", self.restore_cb.isChecked())
        self.settings.setValue("brick_cb", self.brick_cb.isChecked())
        self.settings.setValue("file_radar_cb", self.file_radar_cb.isChecked())
        self.settings.setValue("generate_issuebot_report_cb", self.generate_issuebot_report_cb.isChecked())
        self.settings.setValue("keep_panic_cb", self.keep_panic_cb.isChecked())
        self.settings.setValue("ignore_panic_cb", self.ignore_panic_cb.isChecked())
        self.settings.setValue("wait_for_bundle_cb", self.wait_for_bundle_cb.isChecked())
        self.settings.setValue("comp_name", self.comp_name_input.text().strip())
        self.settings.setValue("comp_version", self.comp_version_input.text().strip())
        self.settings.setValue("milestone", self.milestone_input.text().strip())
        self.settings.setValue("events", self.events_input.text().strip())
        self.settings.setValue("upload_radar", self.upload_radar_input.text().strip())
        self.settings.setValue("device_type", self.device_type_input.text().strip())
        self.settings.setValue("prkit_id", self.prkit_id_input.text().strip())
        self.settings.setValue("atp_kit_id", self.atp_kit_id_input.text().strip())
        self.settings.setValue("prkit_cb", self.prkit_cb.isChecked())
        
        super().closeEvent(event)

    def setup_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        app_layout = QVBoxLayout(central_widget)
        
        self.tabs = QTabWidget()
        app_layout.addWidget(self.tabs)
        
        settings_tab = QWidget()
        self.tabs.addTab(settings_tab, "User Settings")
        
        main_layout = QVBoxLayout(settings_tab)
        main_layout.setContentsMargins(10, 10, 10, 10)
        
        # --- Connection Frame ---
        conn_group = QGroupBox("1. Connection Settings")
        conn_layout = QHBoxLayout()
        
        conn_layout.addWidget(QLabel("Host IP:"))
        self.host_input = HistoryComboBox()
        conn_layout.addWidget(self.host_input)
        
        conn_layout.addWidget(QLabel("User:"))
        self.user_input = HistoryComboBox("gdqe")
        conn_layout.addWidget(self.user_input)
        
        conn_layout.addWidget(QLabel("Password:"))
        self.password_input = QLineEdit()
        self.password_input.setEchoMode(QLineEdit.EchoMode.Password)
        conn_layout.addWidget(self.password_input)
        
        self.fetch_btn = QPushButton("Fetch Devices")
        self.fetch_btn.clicked.connect(self.fetch_devices)
        conn_layout.addWidget(self.fetch_btn)
        
        conn_group.setLayout(conn_layout)
        main_layout.addWidget(conn_group)
        
        # --- Devices Frame ---
        dev_group = QGroupBox("2. Attached Devices (Select to Restore)")
        dev_layout = QVBoxLayout()
        
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["ECID", "Model", "Serial Number", "Primate Path"])
        self.tree.setSelectionMode(QTreeWidget.SelectionMode.ExtendedSelection)
        self.tree.header().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        dev_layout.addWidget(self.tree)
        
        dev_group.setLayout(dev_layout)
        main_layout.addWidget(dev_group, stretch=1)
        
        # --- Configuration Frame ---
        cfg_group = QGroupBox("3. Restore Configuration")
        cfg_layout = QVBoxLayout()
        
        h0 = QHBoxLayout()
        self.restore_cb = QCheckBox("Restore Units and Start Test")
        self.restore_cb.setChecked(True)
        self.restore_cb.toggled.connect(self.toggle_restore_fields)
        h0.addWidget(self.restore_cb)
        
        self.restore_only_cb = QCheckBox("Restore Only")
        self.restore_only_cb.setChecked(False)
        self.restore_only_cb.toggled.connect(self.toggle_restore_only_fields)
        h0.addWidget(self.restore_only_cb)
        
        self.load_recipe_btn = QPushButton("Load Recipe")
        self.load_recipe_btn.clicked.connect(self.load_recipe)
        h0.addWidget(self.load_recipe_btn)

        self.save_recipe_btn = QPushButton("Save Recipe")
        self.save_recipe_btn.clicked.connect(self.save_recipe)
        h0.addWidget(self.save_recipe_btn)
        
        cfg_layout.addLayout(h0)
        
        h_bundle_radar = QHBoxLayout()
        h_bundle_radar.addWidget(QLabel("Bundle ID (Optional):"))
        self.bundle_input = HistoryComboBox()
        h_bundle_radar.addWidget(self.bundle_input)
        
        self.wait_for_bundle_cb = QCheckBox("Wait for Bundle")
        self.wait_for_bundle_cb.setChecked(False)
        h_bundle_radar.addWidget(self.wait_for_bundle_cb)
        
        h_bundle_radar.addWidget(QLabel("Radar IDs (Optional):"))
        self.radars_input = HistoryComboBox()
        h_bundle_radar.addWidget(self.radars_input)
        cfg_layout.addLayout(h_bundle_radar)
        
        h_sequence = QHBoxLayout()
        h_sequence.addWidget(QLabel("Sequence (e.g. @osdiags/burnin):"))
        self.sequence_input = HistoryComboBox()
        h_sequence.addWidget(self.sequence_input)
        cfg_layout.addLayout(h_sequence)
        
        h_log = QHBoxLayout()
        h_log.addWidget(QLabel("Log Save Path (Required):"))
        self.save_path_input = HistoryComboBox()
        h_log.addWidget(self.save_path_input)
        h_log.addWidget(QLabel("Upload To Radar (Optional):"))
        self.upload_radar_input = HistoryComboBox()
        h_log.addWidget(self.upload_radar_input)
        cfg_layout.addLayout(h_log)
        
        h_base = QHBoxLayout()
        h_base.addWidget(QLabel("Base Recipe Path:"))
        self.base_recipe_input = HistoryComboBox("~/Desktop/V68_SW-DOWNLOAD-0222.pr")
        h_base.addWidget(self.base_recipe_input)
        cfg_layout.addLayout(h_base)
        
        self.h_prkit = QHBoxLayout()
        
        self.prkit_cb = QCheckBox("Use PRKit")
        self.prkit_cb.setChecked(False)
        self.prkit_cb.toggled.connect(self.toggle_prkit_fields)
        self.h_prkit.addWidget(self.prkit_cb)

        self.h_prkit.addWidget(QLabel("Devices (e.g. d23ap):"))
        self.device_type_input = HistoryComboBox()
        self.h_prkit.addWidget(self.device_type_input)
        
        self.h_prkit.addWidget(QLabel("BATS Kit ID:"))
        self.prkit_id_input = HistoryComboBox()
        self.h_prkit.addWidget(self.prkit_id_input)
        
        self.h_prkit.addWidget(QLabel("ATP Kit ID:"))
        self.atp_kit_id_input = HistoryComboBox()
        self.h_prkit.addWidget(self.atp_kit_id_input)
        
        cfg_layout.addLayout(self.h_prkit)
        
        h3 = QHBoxLayout()
        self.brick_cb = QCheckBox("Units Powered by Brick")
        self.brick_cb.setChecked(True)
        h3.addWidget(self.brick_cb)
        
        self.keep_panic_cb = QCheckBox("Keep Panic/Stuck State")
        self.keep_panic_cb.setChecked(False)
        h3.addWidget(self.keep_panic_cb)
        self.ignore_panic_cb = QCheckBox("Ignore Panic/Stuck State")
        self.ignore_panic_cb.setChecked(False)
        h3.addWidget(self.ignore_panic_cb)
        
        self.file_radar_cb = QCheckBox("File Unit Radar")
        self.file_radar_cb.toggled.connect(self.on_file_radar_toggled)
        h3.addWidget(self.file_radar_cb)
        
        self.generate_issuebot_report_cb = QCheckBox("Generate Issuebot Report")
        self.generate_issuebot_report_cb.toggled.connect(self.on_generate_issuebot_report_toggled)
        h3.addWidget(self.generate_issuebot_report_cb)
        
        h3.addStretch()
        cfg_layout.addLayout(h3)
        
        h_email = QHBoxLayout()
        h_email.addWidget(QLabel("Notification Email(s) (comma-separated):"))
        self.email_input = HistoryComboBox()
        h_email.addWidget(self.email_input)
        cfg_layout.addLayout(h_email)
        
        h_comp = QHBoxLayout()
        h_comp.addWidget(QLabel("Radar Component Name:"))
        self.comp_name_input = HistoryComboBox("iOS FBR SQA")
        h_comp.addWidget(self.comp_name_input)
        h_comp.addWidget(QLabel("Radar Component Version:"))
        self.comp_version_input = HistoryComboBox("Burnin")
        h_comp.addWidget(self.comp_version_input)
        
        h_comp.addWidget(QLabel("Radar Milestone:"))
        self.milestone_input = HistoryComboBox("")
        h_comp.addWidget(self.milestone_input)
        
        h_comp.addWidget(QLabel("Radar Events:"))
        self.events_input = HistoryComboBox("")
        h_comp.addWidget(self.events_input)
        
        cfg_layout.addLayout(h_comp)
        
        cfg_layout.addWidget(QLabel("Extra Serial Commands (one per line):"))
        
        btn_layout = QHBoxLayout()
        btn_astro_status = QPushButton("+ Clean Astro Status")
        btn_astro_status.clicked.connect(lambda: self.extra_cmds_input.append("astro reset --all"))
        btn_astro_logs = QPushButton("+ Clean Astro Logs")
        btn_astro_logs.clicked.connect(lambda: self.extra_cmds_input.append("rm -rf /var/logs/Astro/*"))
        btn_cb_erase = QPushButton("+ CB Erase")
        btn_cb_erase.clicked.connect(lambda: self.extra_cmds_input.append("controlbits erase -s all"))
        btn_log_collector = QPushButton("+ Clean LogCollector")
        btn_log_collector.clicked.connect(lambda: self.extra_cmds_input.append("rm -rf /var/mobile/Media/FactoryLogs/LogCollector"))
        btn_nvram_iter = QPushButton("+ Set NVRAM Iterations")
        btn_nvram_iter.clicked.connect(lambda: self.extra_cmds_input.append('nvram astro_parameters="{LoopIterations=50}"'))
        
        btn_layout.addWidget(btn_astro_status)
        btn_layout.addWidget(btn_astro_logs)
        btn_layout.addWidget(btn_cb_erase)
        btn_layout.addWidget(btn_log_collector)
        btn_layout.addWidget(btn_nvram_iter)
        cfg_layout.addLayout(btn_layout)
        
        self.extra_cmds_input = QTextEdit()
        self.extra_cmds_input.setFixedHeight(60)
        cfg_layout.addWidget(self.extra_cmds_input)
        
        cfg_group.setLayout(cfg_layout)
        main_layout.addWidget(cfg_group)
        
        # --- Run Button & Clear History ---
        main_layout.addSpacing(10)
        
        btn_layout = QHBoxLayout()
        
        self.run_btn = QPushButton("🚀 START")
        self.run_btn.setMinimumHeight(45)
        self.run_btn.setStyleSheet("background-color: #007AFF; color: white; font-weight: bold; padding: 10px; border-radius: 6px;")
        self.run_btn.clicked.connect(self.start_restore)
        btn_layout.addWidget(self.run_btn, stretch=3)

        self.check_status_btn = QPushButton("🔍 Check Restore Status")
        self.check_status_btn.setMinimumHeight(45)
        self.check_status_btn.setStyleSheet("background-color: #5AC8FA; color: white; font-weight: bold; padding: 10px; border-radius: 6px;")
        self.check_status_btn.clicked.connect(self.check_restore_status)
        btn_layout.addWidget(self.check_status_btn, stretch=2)

        self.clear_btn = QPushButton("🗑️ Clear History")
        self.clear_btn.setMinimumHeight(45)
        self.clear_btn.setStyleSheet("background-color: #FF3B30; color: white; font-weight: bold; padding: 10px; border-radius: 6px;")
        self.clear_btn.clicked.connect(self.clear_all_history)
        btn_layout.addWidget(self.clear_btn, stretch=1)
        
        main_layout.addLayout(btn_layout)
        main_layout.addSpacing(10)
        
        # --- Logs Tab ---
        logs_tab = QWidget()
        self.tabs.addTab(logs_tab, "Execution Logs")
        log_layout = QVBoxLayout(logs_tab)
        log_layout.setContentsMargins(10, 10, 10, 10)
        
        # --- Task Status Table ---
        self.task_table = QTableWidget(0, 3)
        self.task_table.setHorizontalHeaderLabels(["Task Details", "Status", "Actions"])
        self.task_table.horizontalHeader().setStretchLastSection(False)
        self.task_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.task_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.task_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.task_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.task_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.task_table.setMinimumHeight(150)
        self.task_table.setMaximumHeight(250)
        log_layout.addWidget(self.task_table)
        
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setStyleSheet("background-color: #1E1E1E; color: #D4D4D4; font-family: Menlo;")
        log_layout.addWidget(self.log_text)
        
        # Add mock tasks to demonstrate the UI (removed)
        
    def add_task_to_table(self, task_desc, status="Pending"):
        row = self.task_table.rowCount()
        self.task_table.insertRow(row)
        
        # Details
        desc_item = QTableWidgetItem(task_desc)
        self.task_table.setItem(row, 0, desc_item)
        
        # Status
        status_item = QTableWidgetItem(status)
        if status == "Running":
            status_item.setForeground(QColor("orange"))
        elif status == "Completed":
            status_item.setForeground(QColor("green"))
        elif status == "Failed":
            status_item.setForeground(QColor("red"))
        elif status == "Halted":
            status_item.setForeground(QColor("magenta"))
        status_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self.task_table.setItem(row, 1, status_item)
        
        # Actions
        action_widget = QWidget()
        action_layout = QHBoxLayout(action_widget)
        action_layout.setContentsMargins(2, 2, 2, 2)
        
        rerun_btn = QPushButton("Re-run")
        halt_btn = QPushButton("Halt")
        
        # Connect buttons to mock functions (to be wired up later)
        rerun_btn.clicked.connect(lambda _, r=row: self.handle_rerun_task(r))
        halt_btn.clicked.connect(lambda _, r=row: self.handle_halt_task(r))
        
        action_layout.addWidget(rerun_btn)
        action_layout.addWidget(halt_btn)
        self.task_table.setCellWidget(row, 2, action_widget)

    def handle_rerun_task(self, row_index):
        self.append_log(f"Re-run clicked for task {row_index+1}", "ACTION", "info")
        self.update_task_status(row_index, "Running")
        
    def handle_halt_task(self, row_index):
        self.append_log(f"Halt clicked for task {row_index+1}", "ACTION", "warning")
        
        # Find the thread associated with this row and terminate it
        thread_to_remove = None
        for thread in self.active_threads:
            if hasattr(thread, 'table_row_index') and thread.table_row_index == row_index:
                self.append_log(f"Forcefully terminating background task thread.", "SYSTEM", "error")
                thread.terminate()
                thread.wait() # wait for it to actually stop
                thread_to_remove = thread
                break
                
        if thread_to_remove:
            self.active_threads.remove(thread_to_remove)
            
        self.update_task_status(row_index, "Halted")

    def update_task_status(self, row_index, new_status):
        if 0 <= row_index < self.task_table.rowCount():
            status_item = self.task_table.item(row_index, 1)
            if status_item:
                status_item.setText(new_status)
                if new_status == "Running":
                    status_item.setForeground(QColor("orange"))
                elif new_status == "Completed":
                    status_item.setForeground(QColor("green"))
                elif new_status == "Failed":
                    status_item.setForeground(QColor("red"))
                elif new_status == "Halted":
                    status_item.setForeground(QColor("magenta"))
                else:
                    status_item.setForeground(QColor("white" if self.palette().color(self.backgroundRole()).lightness() < 128 else "black"))

    def toggle_prkit_fields(self, checked):
        self.device_type_input.setEnabled(checked)
        self.prkit_id_input.setEnabled(checked)
        self.atp_kit_id_input.setEnabled(checked)
        self.base_recipe_input.setEnabled(not checked)

    def toggle_restore_fields(self, checked):
        self.radars_input.setEnabled(checked)
        self.base_recipe_input.setEnabled(checked)
        self.bundle_input.setEnabled(checked)
        
        if hasattr(self, 'restore_only_cb'):
            self.restore_only_cb.setEnabled(checked)
            if not checked:
                self.restore_only_cb.setChecked(False)
                
        if hasattr(self, 'run_btn'):
            if checked:
                if hasattr(self, 'restore_only_cb') and self.restore_only_cb.isChecked():
                    self.run_btn.setText("🚀 START (NO BOOTARGS)")
                else:
                    self.run_btn.setText("🚀 START")
            else:
                self.run_btn.setText("🚀 START BOOTARGS (NO RESTORE)")

    def toggle_restore_only_fields(self, checked):
        self.sequence_input.setEnabled(not checked)
        self.brick_cb.setEnabled(not checked)
        self.keep_panic_cb.setEnabled(not checked)
        self.ignore_panic_cb.setEnabled(not checked)
        self.file_radar_cb.setEnabled(not checked)
        self.generate_issuebot_report_cb.setEnabled(not checked)
        
        if checked:
            self.brick_cb.setChecked(False)
            self.keep_panic_cb.setChecked(False)
            self.ignore_panic_cb.setChecked(False)
            self.file_radar_cb.setChecked(False)
            self.generate_issuebot_report_cb.setChecked(False)

        if hasattr(self, 'run_btn'):
            if checked:
                self.run_btn.setText("🚀 START (NO BOOTARGS)")
            else:
                if self.restore_cb.isChecked():
                    self.run_btn.setText("🚀 START")

    def on_file_radar_toggled(self, checked):
        if checked:
            self.generate_issuebot_report_cb.blockSignals(True)
            self.generate_issuebot_report_cb.setChecked(False)
            self.generate_issuebot_report_cb.blockSignals(False)
        self.toggle_file_radar_fields()

    def on_generate_issuebot_report_toggled(self, checked):
        if checked:
            self.file_radar_cb.blockSignals(True)
            self.file_radar_cb.setChecked(False)
            self.file_radar_cb.blockSignals(False)
        self.toggle_file_radar_fields()

    def toggle_file_radar_fields(self, checked=False):
        any_checked = self.file_radar_cb.isChecked() or self.generate_issuebot_report_cb.isChecked()
        self.comp_name_input.setEnabled(any_checked)
        self.comp_version_input.setEnabled(any_checked)
        self.milestone_input.setEnabled(any_checked)
        self.events_input.setEnabled(any_checked)

    def show_popup_msg(self, title, message):
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QTextEdit, QPushButton
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.resize(650, 400)
        
        layout = QVBoxLayout(dialog)
        
        text_edit = QTextEdit(dialog)
        text_edit.setReadOnly(True)
        text_edit.setStyleSheet("background-color: #1E1E1E; color: #D4D4D4; font-family: Menlo;")
        text_edit.setPlainText(message)
        
        layout.addWidget(text_edit)
        
        btn = QPushButton("OK", dialog)
        btn.clicked.connect(dialog.accept)
        layout.addWidget(btn)
        
        dialog.exec()

    def append_log(self, message, prefix="INFO", level="info"):
        timestamp = datetime.now().strftime("%H:%M:%S")
        color = "#D4D4D4" # Default info
        bold = False
        
        if level == "success":
            color = "#4CAF50"
            bold = True
        elif level == "warning":
            color = "#FFC107"
        elif level == "error":
            color = "#F44336"
            bold = True
            
        weight = "bold" if bold else "normal"
        html_line = f'<span style="color: {color}; font-weight: {weight};">[{timestamp}] [{prefix}] {message}</span>'
        self.log_text.append(html_line)
        
    def fetch_devices(self):
        host = self.host_input.text().strip()
        user = self.user_input.text().strip()
        password = self.password_input.text()
        
        if not host or not password:
            QMessageBox.critical(self, "Input Error", "Host IP and Password are required to fetch devices.")
            return
            
        self.fetch_btn.setEnabled(False)
        self.append_log(f"Connecting to {host}...", "DISCOVERY", "info")
        
        self.fetch_thread = FetchDevicesThread(host, user, password)
        self.fetch_thread.finished.connect(self.on_fetch_finished)
        self.fetch_thread.start()
        
    def on_fetch_finished(self, devices, err_msg):
        self.fetch_btn.setEnabled(True)
        self.tree.clear()
        
        if err_msg:
            self.append_log(f"Failed to fetch devices: {err_msg}", "ERROR", "error")
            return
            
        self.devices = devices
        if not self.devices:
            self.append_log("No attached CAMEmbeddedDeviceResource units found.", "DISCOVERY", "warning")
            return
            
        for dev in self.devices:
            item = QTreeWidgetItem([dev['ecid'], dev['model'], dev['serial'], dev['primatePath']])
            self.tree.addTopLevelItem(item)
            item.setSelected(True)
            
        self.append_log(f"Found {len(self.devices)} device(s).", "DISCOVERY", "success")
        

    def clear_all_history(self):
        for field, key in [
            (self.host_input, "host"),
            (self.user_input, "user"),
            (self.radars_input, "radars"),
            (self.sequence_input, "sequence"),
            (self.base_recipe_input, "base_recipe"),
            (self.bundle_input, "bundle_id"),
            (self.save_path_input, "save_path"),
            (self.email_input, "email"),
            (self.comp_name_input, "comp_name"),
            (self.comp_version_input, "comp_version"),
            (self.device_type_input, "device_type"),
            (self.prkit_id_input, "prkit_id"),
            (self.atp_kit_id_input, "atp_kit_id"),
            (self.upload_radar_input, "upload_radar"),
            (self.milestone_input, "milestone"),
            (self.events_input, "events")
        ]:
            field.clear()
            self.settings.remove(f"{key}_history")
        self.append_log("All input history has been cleared.", "SYSTEM", "warning")

    def check_restore_status(self):
        selected_items = self.tree.selectedItems()
        if not selected_items:
            QMessageBox.information(self, "Info", "Please select at least one device to check status.")
            return

        host = self.host_input.text().strip()
        user = self.user_input.text().strip()
        password = self.password_input.text()
        
        if not all([host, user, password]):
            QMessageBox.critical(self, "Error", "Host, user, and password must be provided to check status.")
            return

        import json
        for item in selected_items:
            ecid = item.text(0)
            remote_status_file = f"/tmp/restore_status_{ecid}.json"
            
            cat_cmd = ["sshpass", "-p", password, "ssh", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=5", f"{user}@{host}", f"cat {remote_status_file}"]
            cat_result = subprocess.run(cat_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            
            if cat_result.returncode != 0:
                QMessageBox.warning(self, "Status Not Found", f"No active/recent restore status found for ECID: {ecid[-6:]}.\nIt may not have started yet, or the device was not restored.")
                continue
                
            try:
                data = json.loads(cat_result.stdout.strip())
                status = data.get("status", "UNKNOWN")
                progress = data.get("progress", "")
                log_snippet = "\n".join(data.get("log", [])[-10:])
                
                msg = f"Status: {status}\nProgress: {progress}\n\nRecent Logs:\n{log_snippet}"
                QMessageBox.information(self, f"Restore Status - {ecid[-6:]}", msg)
            except json.JSONDecodeError:
                QMessageBox.warning(self, "Error", f"Failed to parse status file for ECID: {ecid[-6:]}")

    def start_restore(self):
        
        # Update histories in UI
        for field in [self.host_input, self.user_input, self.radars_input, self.sequence_input, 
                      self.base_recipe_input, self.bundle_input, self.save_path_input, self.email_input, 
                      self.comp_name_input, self.comp_version_input, self.device_type_input, 
                      self.prkit_id_input, self.atp_kit_id_input, self.upload_radar_input,
                      self.milestone_input, self.events_input]:
            field.add_to_history()

        selected_items = self.tree.selectedItems()
        if not selected_items:
            QMessageBox.critical(self, "Selection Error", "Please select at least one device to proceed.")
            return
            
        host = self.host_input.text().strip()
        user = self.user_input.text().strip()
        password = self.password_input.text()
        radars = self.radars_input.text().strip()
        sequence = self.sequence_input.text().strip()
        base_recipe = self.base_recipe_input.text().strip()
        save_path = self.save_path_input.text().strip()
        do_restore = self.restore_cb.isChecked()
        restore_only = self.restore_only_cb.isChecked()
        use_prkit = self.prkit_cb.isChecked()
        device_type = self.device_type_input.text().strip().lower()
        prkit_id = self.prkit_id_input.text().strip()
        atp_kit_id = self.atp_kit_id_input.text().strip()
        
        if do_restore and use_prkit:
            if not device_type:
                QMessageBox.critical(self, "Input Error", "Device Type is required when using PRKit.")
                return
            if (prkit_id and atp_kit_id) or (not prkit_id and not atp_kit_id):
                QMessageBox.critical(self, "Input Error", "Exactly ONE of BATS Kit ID or ATP Kit ID must be provided (not both).")
                return

        required_fields = [host, password, save_path]
        if not restore_only:
            required_fields.append(sequence)
        if not use_prkit and do_restore:
            required_fields.append(base_recipe)
            
        if not all(required_fields):
            msg = "Host, Password, Save Path, Sequence (if applying bootargs), and Base Recipe (if restoring without PRKit) are required."
            QMessageBox.critical(self, "Input Error", msg)
            return

        any_radar_action = self.file_radar_cb.isChecked() or self.generate_issuebot_report_cb.isChecked()
        if any_radar_action:
            if not self.comp_name_input.text().strip() or not self.comp_version_input.text().strip():
                QMessageBox.critical(self, "Input Error", "Radar Component Name and Radar Component Version are required when File Unit Radar or Generate Issuebot Report is checked.")
                return
            
        selected_devices = []
        for item in selected_items:
            ecid = item.text(0)
            dev = next((d for d in self.devices if d['ecid'] == ecid), None)
            if dev: selected_devices.append(dev)
            
        extra_cmds = [cmd.strip() for cmd in self.extra_cmds_input.toPlainText().strip().split('\n') if cmd.strip()]
        
        # --- Check 1: Active Device Busy Warning ---
        running_ecids = set()
        for active_t in self.active_threads:
            for dev in active_t.selected_devices:
                running_ecids.add(dev['ecid'])
                
        busy_devices = []
        for dev in selected_devices:
            if dev['ecid'] in running_ecids:
                busy_devices.append(f"• Serial: {dev['serial']} (ECID: {dev['ecid']})")
                
        if busy_devices:
            dev_list_str = "\n".join(busy_devices)
            reply = QMessageBox.question(
                self,
                "Device Busy Warning",
                f"The following selected device(s) are already running in an active task:\n\n{dev_list_str}\n\nDo you want to run another task on them concurrently?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        # --- Check 2: Unchanged Parameters Check ---
        current_params = {
            "host": host,
            "user": user,
            "password": password,
            "radars": radars,
            "sequence": sequence,
            "base_recipe": base_recipe,
            "save_path": save_path,
            "do_restore": do_restore,
            "bundle_id": self.bundle_input.text().strip(),
            "wait_for_bundle": self.wait_for_bundle_cb.isChecked(),
            "is_brick": self.brick_cb.isChecked(),
            "keep_panic": self.keep_panic_cb.isChecked(),
            "ignore_panic": self.ignore_panic_cb.isChecked(),
            "extra_cmds": extra_cmds,
            "use_prkit": use_prkit,
            "device_type": device_type,
            "prkit_id": prkit_id,
            "atp_kit_id": atp_kit_id,
            "file_radar": self.file_radar_cb.isChecked(),
            "generate_issuebot": self.generate_issuebot_report_cb.isChecked(),
            "milestone": self.milestone_input.text().strip(),
            "events": self.events_input.text().strip(),
            "selected_ecids": sorted([dev['ecid'] for dev in selected_devices])
        }
        
        if self.last_launched_params is not None:
            if current_params == self.last_launched_params:
                reply = QMessageBox.question(
                    self,
                    "Duplicate Task Warning",
                    "No parameters or device selections have been changed since the last launched task.\n\nDo you want to run the exact same task again?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return

        # Update last launched parameters
        self.last_launched_params = current_params
        
        # Launch new parallel operation
        self.append_log(f"Launching a new parallel pipeline session for {len(selected_devices)} device(s)...", "SYSTEM", "success")
        
        restore_thread = RestoreWorkerThread(
            host, user, password, selected_devices, radars, sequence, base_recipe, 
            self.bundle_input.text().strip(), save_path, self.brick_cb.isChecked(), extra_cmds, self.logger_signal, do_restore, restore_only, self.email_input.text().strip(), self.file_radar_cb.isChecked(), self.comp_name_input.text().strip(), self.comp_version_input.text().strip(), self.keep_panic_cb.isChecked(), self.ignore_panic_cb.isChecked(),
            use_prkit, device_type, prkit_id, atp_kit_id, upload_radar=self.upload_radar_input.text().strip(),
            generate_issuebot=self.generate_issuebot_report_cb.isChecked(), milestone=self.milestone_input.text().strip(), events=self.events_input.text().strip(), wait_for_bundle=self.wait_for_bundle_cb.isChecked()
        )
        
        self.active_threads.add(restore_thread)
        
        # Track the task in the UI
        self.task_counter += 1
        bundle_id = self.bundle_input.text().strip()
        if restore_only:
            task_action = "Restore Only"
        elif do_restore:
            task_action = "Restore Units and Start Test"
        else:
            task_action = "Start Test"
            
        desc_parts = [f"Task #{self.task_counter} {task_action}"]
        if sequence:
            desc_parts.append(f"Seq {sequence}")
        if bundle_id:
            desc_parts.append(bundle_id)
            
        task_desc = " - ".join(desc_parts)
        
        row_index = self.task_table.rowCount()
        self.add_task_to_table(task_desc, "Running")
        
        # Store row_index onto thread to update it later
        restore_thread.table_row_index = row_index
        
        restore_thread.finished.connect(lambda t=restore_thread: self.on_thread_finished(t))
        restore_thread.start()
        
    def on_thread_finished(self, thread):
        if thread in self.active_threads:
            self.active_threads.remove(thread)
            
        if hasattr(thread, 'table_row_index'):
            self.update_task_status(thread.table_row_index, "Completed")
            
        self.append_log("A parallel operation pipeline has completed.", "SYSTEM", "success")

    def save_recipe(self):
        recipe = {
            "do_restore": self.restore_cb.isChecked(),
            "restore_only": self.restore_only_cb.isChecked(),
            "bundle_id": self.bundle_input.text().strip(),
            "wait_for_bundle": self.wait_for_bundle_cb.isChecked(),
            "radars": self.radars_input.text().strip(),
            "sequence": self.sequence_input.text().strip(),
            "save_path": self.save_path_input.text().strip(),
            "upload_radar": self.upload_radar_input.text().strip(),
            "base_recipe": self.base_recipe_input.text().strip(),
            "use_prkit": self.prkit_cb.isChecked(),
            "device_type": self.device_type_input.text().strip(),
            "prkit_id": self.prkit_id_input.text().strip(),
            "atp_kit_id": self.atp_kit_id_input.text().strip(),
            "is_brick": self.brick_cb.isChecked(),
            "keep_panic": self.keep_panic_cb.isChecked(),
            "ignore_panic": self.ignore_panic_cb.isChecked(),
            "file_radar": self.file_radar_cb.isChecked(),
            "generate_issuebot": self.generate_issuebot_report_cb.isChecked(),
            "email": self.email_input.text().strip(),
            "extra_cmds": self.extra_cmds_input.toPlainText().strip(),
            "comp_name": self.comp_name_input.text().strip(),
            "comp_version": self.comp_version_input.text().strip(),
            "milestone": self.milestone_input.text().strip(),
            "events": self.events_input.text().strip()
        }
        
        file_path, _ = QFileDialog.getSaveFileName(self, "Save Recipe", "recipe.json", "JSON Files (*.json)")
        if file_path:
            try:
                with open(file_path, 'w') as f:
                    json.dump(recipe, f, indent=4)
                self.append_log(f"Recipe saved to {file_path}", "SYSTEM", "success")
            except Exception as e:
                QMessageBox.critical(self, "Error Saving Recipe", f"Failed to save recipe:\n{str(e)}")

    def load_recipe(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Load Recipe", "", "JSON Files (*.json)")
        if not file_path:
            return
            
        try:
            with open(file_path, 'r') as f:
                recipe = json.load(f)
                
            if recipe.get("file_radar") and recipe.get("generate_issuebot"):
                QMessageBox.warning(self, "Conflict in Recipe", "Recipe contains both 'File Unit Radar' and 'Generate Issuebot Report' which conflict. Unchecking 'Generate Issuebot Report'.")
                recipe["generate_issuebot"] = False
                
            self.restore_cb.setChecked(recipe.get("do_restore", True))
            self.restore_only_cb.setChecked(recipe.get("restore_only", False))
            self.bundle_input.setText(recipe.get("bundle_id", ""))
            self.wait_for_bundle_cb.setChecked(recipe.get("wait_for_bundle", False))
            self.radars_input.setText(recipe.get("radars", ""))
            self.sequence_input.setText(recipe.get("sequence", ""))
            self.save_path_input.setText(recipe.get("save_path", ""))
            self.upload_radar_input.setText(recipe.get("upload_radar", ""))
            self.base_recipe_input.setText(recipe.get("base_recipe", "~/Desktop/V68_SW-DOWNLOAD-0222.pr"))
            self.prkit_cb.setChecked(recipe.get("use_prkit", False))
            self.device_type_input.setText(recipe.get("device_type", ""))
            self.prkit_id_input.setText(recipe.get("prkit_id", ""))
            self.atp_kit_id_input.setText(recipe.get("atp_kit_id", ""))
            self.brick_cb.setChecked(recipe.get("is_brick", True))
            self.keep_panic_cb.setChecked(recipe.get("keep_panic", False))
            self.ignore_panic_cb.setChecked(recipe.get("ignore_panic", False))
            self.file_radar_cb.setChecked(recipe.get("file_radar", False))
            self.generate_issuebot_report_cb.setChecked(recipe.get("generate_issuebot", False))
            self.email_input.setText(recipe.get("email", ""))
            self.extra_cmds_input.setPlainText(recipe.get("extra_cmds", ""))
            self.comp_name_input.setText(recipe.get("comp_name", "iOS FBR SQA"))
            self.comp_version_input.setText(recipe.get("comp_version", "Burnin"))
            self.milestone_input.setText(recipe.get("milestone", ""))
            self.events_input.setText(recipe.get("events", ""))
            
            self.append_log(f"Recipe loaded from {file_path}", "SYSTEM", "success")
        except Exception as e:
            QMessageBox.critical(self, "Error Loading Recipe", f"Failed to load recipe:\n{str(e)}")

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
    