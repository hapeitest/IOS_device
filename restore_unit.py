#!/usr/bin/env python3
import sys
import subprocess
import json
import time
import os
import re

def save_status(filepath, status, progress, output_log=[]):
    data = {
        "status": status,
        "progress": progress,
        "log": output_log[-50:],
        "timestamp": time.time()
    }
    with open(filepath, 'w') as f:
        json.dump(data, f)

def main():
    if len(sys.argv) < 5:
        print("Usage: python3 restore_unit.py <ecid> <primate_path> <recipe_path> <status_file>")
        sys.exit(1)

    ecid = sys.argv[1]
    primate_path = sys.argv[2]
    recipe_path = sys.argv[3]
    status_file = sys.argv[4]

    output_log = []

    # 1. Put device in DFU mode
    save_status(status_file, "DFU", "Putting device in DFU mode...", output_log)
    dfu_cmd = ["/usr/local/bin/astrisctl", "--probe", primate_path, "dfu"]
    output_log.append(f"Running: {' '.join(dfu_cmd)}")
    
    dfu_process = subprocess.run(dfu_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if dfu_process.stdout:
        output_log.extend(dfu_process.stdout.split('\n'))
    
    time.sleep(3)

    # 2. Run mobile_restore
    save_status(status_file, "RESTORING", "Starting mobile_restore...", output_log)
    
    if recipe_path.startswith("~"):
        recipe_path = os.path.expanduser(recipe_path)
    
    recipe_dir = os.path.dirname(recipe_path) or "."
    restore_cmd = ["/usr/local/bin/mobile_restore", "--restore", "-e", ecid, "-D", recipe_path, "-K"]
    
    output_log.append(f"Running: {' '.join(restore_cmd)} in {recipe_dir}")
    
    try:
        process = subprocess.Popen(
            restore_cmd,
            cwd=recipe_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True
        )

        for line in process.stdout:
            cleaned_line = line.strip()
            if cleaned_line:
                output_log.append(cleaned_line)
                # Keep log size reasonable
                if len(output_log) > 100:
                    output_log = output_log[-100:]
                
                # Update status if the line indicates progress
                if any(k in cleaned_line for k in ["Progress:", "Percentage", "Entering", "State:", "Status:", "Restoring", "Error", "Failed", "Exception"]):
                    save_status(status_file, "RESTORING", cleaned_line, output_log)

        process.wait()
        
        if process.returncode == 0:
            save_status(status_file, "COMPLETED", "Restore completed successfully.", output_log)
        else:
            save_status(status_file, "FAILED", f"Restore failed with return code {process.returncode}.", output_log)
            
    except Exception as e:
        output_log.append(f"Exception during restore: {str(e)}")
        save_status(status_file, "FAILED", f"Exception: {str(e)}", output_log)

if __name__ == '__main__':
    main()
