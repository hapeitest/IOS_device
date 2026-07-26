#!/usr/bin/env python3
import os
import re
import sys
import glob
import shlex
import shutil
import plistlib
import argparse
import subprocess
from datetime import datetime

# --- Apple Internal Radar Client ---
try:
    from radarclient import RadarClient, ClientSystemIdentifier
except ImportError:
    print("Error: radarclient module not found.")
    print("Please run: pip3 install radarclient --user -i https://pypi.apple.com/simple")
    sys.exit(1)

# --- Configuration & Defaults ---
DEFAULT_PR_DOC = "/Users/burnin_i/Desktop/quick_prkit/V6x_DVT.pr"
DEFAULT_RADARS = ["113247587", "114006481", "128241608"]

class RootAutomator:
    def __init__(self, pr_doc_path, radar_list, save_path=None):
        self.pr_doc_path = pr_doc_path
        self.radar_list = radar_list
        self.timestamp = datetime.now().strftime("%Y-%m-%d_%H_%M_%S")
        
        # Keep roots exactly where they were originally (no changes here)
        if "developer.pr" in self.pr_doc_path:
            self.radar_saved_path = os.path.join(os.path.dirname(self.pr_doc_path), "roots")
        else:
            self.radar_saved_path = f"/tmp/Roots_{self.timestamp}/"
            
        # CHANGE: If the user provided a save_path, put the final PRDoc there!
        if save_path:
            self.modified_pr_doc_path = os.path.join(save_path, f"QuickPR_{self.timestamp}.pr")
        else:
            self.modified_pr_doc_path = os.path.join(os.path.dirname(self.pr_doc_path), f"QuickPR_{self.timestamp}.pr")
            
        self.downloaded_roots = []
        
        # Create directories
        os.makedirs(self.radar_saved_path, exist_ok=True)
        self.merged_roots_path = os.path.join(self.radar_saved_path, "merged_roots")
        self.other_root_path = os.path.join(self.radar_saved_path, "otherroot")
        self.bbfw_root_path = os.path.join(self.radar_saved_path, "BBFW_ROOT")
        os.makedirs(self.merged_roots_path, exist_ok=True)
        os.makedirs(self.other_root_path, exist_ok=True)

        # Authenticate with Radar
        print("Initializing RadarClient via AppleConnect...")
        my_identifier = ClientSystemIdentifier(name="SQA-Automation-Tool", version="1.0")
        self.client = RadarClient.radarclient_for_current_appleconnect_session(
            system_identifier=my_identifier
        )

    def run_cmd(self, cmd, capture_output=True):
        """Helper to run shell commands safely (used for ditto merging)."""
        try:
            result = subprocess.run(cmd, shell=True, capture_output=capture_output, text=True)
            if result.returncode != 0:
                print(f"\n⚠️ WARNING: Command failed (Exit {result.returncode})\nCommand: {cmd}")
            return result.stdout.strip() if capture_output else result.returncode
        except Exception as e:
            print(f"\n⚠️ Failed to execute: {cmd}\nException: {e}\n")
            return ""

    def download_file(self, attachment):
        """Streams an attachment safely to the tmp directory."""
        print(f"Downloading '{attachment.fileName}' to {self.radar_saved_path} ...")
        save_path = os.path.join(self.radar_saved_path, attachment.fileName)
        
        # Stream download directly to file handle to prevent RAM crashes
        with open(save_path, "wb") as f:
            attachment.write_to_file(f)
            
        self.downloaded_roots.append(attachment.fileName)

    # --- Stage 1: Download Logic ---
    def fetch_roots(self):
        print("\n------------------Finding and Downloading Roots Starts------------------\n")
        
        for item in self.radar_list:
            if "+" in item:
                radar_id, pattern = item.split("+", 1)
                print(f"########## Now at rdar://{radar_id} with pattern {pattern} ##########")
                self._download_with_pattern(radar_id, pattern)
            else:
                radar_id = item
                print(f"########## Now at rdar://{radar_id} ##########")
                self._download_latest(radar_id)

        # Output manifest of downloaded roots
        print("\nRoots have been downloaded to local host:")
        with open(os.path.join(self.radar_saved_path, "root_info.txt"), "w") as f:
            for root in self.downloaded_roots:
                print(root)
                f.write(f"{root}\n")
        print("\n------------------Finding and Downloading Roots Ends------------------\n")

    def _download_latest(self, radar_id):
        """Equivalent of find_and_download_attachment in bash."""
        radar = self.client.radar_for_id(radar_id)
        attachments = list(radar.attachments.items())
        
        if not attachments:
            print(f"No files found for {radar_id}")
            return

        # Sort chronologically by AddedAt date
        attachments.sort(key=lambda a: a.addedAt)
        latest_filename = attachments[-1].fileName
        
        # Get unique timestamps to mimic bash `tail -n X` behavior (used for EFI)
        unique_times = sorted(list(set(a.addedAt for a in attachments)))

        if "OSD" in latest_filename:
            print("Detecting this is OSD Root")
            # Dynamically target ExperimentalOS for V68, otherwise standard FactoryOS
            if "V68" in latest_filename.upper():
                target_os = "FactoryExperimentalOS"
            else:
                target_os = "FactoryOS"         
            osd_roots = [a for a in attachments if target_os in a.fileName]
            if osd_roots:
                self.download_file(osd_roots[-1])  # [-1] gets only the absolute latest
            else:
                print(f"No {target_os} root found in this radar.")
                
        elif "LLDIAGS" in latest_filename:
            print("Detecting this is EFI Root")
            last_two_times = unique_times[-2:] if len(unique_times) >= 2 else unique_times
            for a in attachments:
                if a.addedAt in last_two_times:
                    self.download_file(a)
                
        elif "INITIUM" in latest_filename:
            print("Detecting this is BBFW (NEW)")
            bbfw_files = [a for a in attachments if "c4020iphone" in a.fileName.lower()]
            if bbfw_files:
                bbfw_files.sort(key=lambda a: a.fileName) # Sort by version like `sort -V`
                self.download_file(bbfw_files[-1])
        else:
            print("Downloading latest other root")
            self.download_file(attachments[-1])

    def _download_with_pattern(self, radar_id, pattern):
        radar = self.client.radar_for_id(radar_id)
        attachments = list(radar.attachments.items())
        
        for a in attachments:
            if re.search(pattern, a.fileName, re.IGNORECASE):
                print(f"Found Root {a.fileName}")
                if "OSD" in a.fileName and "FactoryRestore" in a.fileName:
                    print("Skipping OSD-FactoryRestore roots")
                else:
                    self.download_file(a)

    # --- Stage 2: Merge Logic ---
    def merge_roots(self):
        print("\n------------------Merging Roots Starts------------------\n")
        self.bbfw_path = ""
        self.midas_path = ""

        archives = glob.glob(os.path.join(self.radar_saved_path, "*.cpgz")) + \
                   glob.glob(os.path.join(self.radar_saved_path, "*.zip"))

        print(f"Found {len(archives)} archive(s) to process.")

        for archive in archives:
            filename = os.path.basename(archive)
            no_ext_name = os.path.splitext(filename)[0]
            extract_dir = os.path.join(self.radar_saved_path, no_ext_name)
            
            # Dynamic flag for Ditto depending on compression format
            unarchive_flag = "-xk" if filename.lower().endswith(".zip") else "-x"
            
            safe_archive = shlex.quote(archive)
            safe_extract_dir = shlex.quote(extract_dir + "/")
            
            # Added "FactoryExperimentalOS" to the allowed unarchive conditions
            if ("FactoryOS" in filename or "FactoryExperimentalOS" in filename) and ("OSD" in filename or "LLDIAGS" in filename):
                print(f"Unarchiving '{filename}' to {extract_dir}/")
                os.makedirs(extract_dir, exist_ok=True)
                self.run_cmd(f"/usr/bin/ditto {unarchive_flag} {safe_archive} {safe_extract_dir}", capture_output=False)
                
                print(f"Merging {extract_dir}/ to merged_roots...")
                self.run_cmd(f"/usr/bin/ditto {shlex.quote(extract_dir)} {shlex.quote(self.merged_roots_path)}", capture_output=False)

            elif "FactoryRestore" in filename and "LLDIAGS" in filename:
                print(f"Unarchiving '{filename}' to {extract_dir}/")
                os.makedirs(extract_dir, exist_ok=True)
                self.run_cmd(f"/usr/bin/ditto {unarchive_flag} {safe_archive} {safe_extract_dir}", capture_output=False)

            elif re.search(r'Mav..-', filename):
                print(f"'{filename}' is OLD VERSION BB firmware, skip merging it...")
                self.bbfw_path = archive

            elif re.search(r'INITIUM..-', filename):
                print(f"'{filename}' is NEW VERSION BB firmware, handle it...")
                os.makedirs(self.bbfw_root_path, exist_ok=True)
                safe_bbfw_root = shlex.quote(self.bbfw_root_path + "/")
                self.run_cmd(f"/usr/bin/ditto {unarchive_flag} {safe_archive} {safe_bbfw_root}", capture_output=False)
                
                for bbfw in os.listdir(self.bbfw_root_path):
                    bbfw_full = os.path.join(self.bbfw_root_path, bbfw)
                    if "INITIUM" in bbfw:
                        self.bbfw_path = bbfw_full
                    if "COFSCD" in bbfw:
                        inner_flag = "-xk" if bbfw.lower().endswith(".zip") else "-x"
                        self.run_cmd(f"/usr/bin/ditto {inner_flag} {shlex.quote(bbfw_full)} {shlex.quote(self.other_root_path)}", capture_output=False)
            else:
                print(f"Unarchiving '{filename}' to otherroot")
                self.run_cmd(f"/usr/bin/ditto {unarchive_flag} {safe_archive} {shlex.quote(self.other_root_path)}", capture_output=False)

        # Merge other roots
        other_dirs = glob.glob(os.path.join(self.other_root_path, "*"))
        if other_dirs:
            for other in other_dirs:
                print(f"Merging '{os.path.basename(other)}' to merged_roots...")
                self.run_cmd(f"/usr/bin/ditto {shlex.quote(other)} {shlex.quote(self.merged_roots_path)}", capture_output=False)

        # Deep search for Midas diag file
        print("Searching recursively for Midas diag-*.im4p...")
        for root_dir, dirs, files in os.walk(self.radar_saved_path):
            for f in files:
                if f.startswith("diag-") and f.endswith(".im4p"):
                    full_path = os.path.join(root_dir, f)
                    if "AppleInternal" in full_path:
                        self.midas_path = full_path
                        print(f"Found Midas: {self.midas_path}")
                        break
            if self.midas_path:
                break
                
        print("\n------------------Merging Roots Ends------------------\n")

    # --- Stage 3: Plist Update Logic ---
    def update_prdoc(self):
        print("\n------------------Updating PRDocs Starts------------------\n")
        
        if not os.path.exists(self.pr_doc_path):
            print(f"⚠️ Base PRDoc not found at {self.pr_doc_path}! Please ensure the path is correct.")
            return
            
        shutil.copy2(self.pr_doc_path, self.modified_pr_doc_path)

        with open(self.modified_pr_doc_path, 'rb') as f:
            plist = plistlib.load(f)

        # Ensure dictionaries exist
        if 'RestoreOptions' not in plist:
            plist['RestoreOptions'] = {}
        if 'BundleOverrides' not in plist['RestoreOptions']:
            plist['RestoreOptions']['BundleOverrides'] = {}
            
        bundle_overrides = plist['RestoreOptions']['BundleOverrides']

        if self.bbfw_path:
            clean_path = self.bbfw_path.replace('//', '/')
            print(f"Updating PRDoc with Baseband FW: {clean_path}")
            bundle_overrides['BasebandFirmware'] = f"file://{clean_path}"

        if self.midas_path:
            clean_path = self.midas_path.replace('//', '/')
            print(f"Updating PRDoc with Midas: {clean_path}")
            bundle_overrides['Diags'] = f"file://{clean_path}"
        else:
            print("Warning: No Midas path found, skipping Diags update.")

        if os.path.exists(self.merged_roots_path) and os.listdir(self.merged_roots_path):
            clean_path = self.merged_roots_path.replace('//', '/')
            print(f"Updating PRDoc with Merged Roots: {clean_path}")
            plist['RestoreOptions']['RootToInstall'] = f"file://{clean_path}"
        else:
            print("⚠️ Warning: merged_roots path is empty or does not exist. Skipping RootToInstall update.")

        with open(self.modified_pr_doc_path, 'wb') as f:
            plistlib.dump(plist, f)

        print(f"\nSuccessfully generated PRDoc: {self.modified_pr_doc_path}")
        print("\n------------------Updating PRDocs Ends------------------\n")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Radar Download and PRDoc Generation Tool")
    parser.add_argument("--prdoc", default=DEFAULT_PR_DOC, help="Path to base PRDoc")
    parser.add_argument("--radars", nargs="+", default=DEFAULT_RADARS, help="List of Radar IDs (space separated)")
    parser.add_argument("--save-path", default=None, help="Directory to save the generated PRDoc")
    args = parser.parse_args()

    automator = RootAutomator(args.prdoc, args.radars, args.save_path)
    automator.fetch_roots()
    automator.merge_roots()
    automator.update_prdoc()