import serial
import time
import os
import json
import threading
import shutil
import re
import logging
from logging.handlers import RotatingFileHandler
from serial.tools import list_ports

# All paths (configs, data, log) are relative to the script folder,
# so it works no matter where it is started from (e.g. systemd)
os.chdir(os.path.dirname(os.path.abspath(__file__)))

# =================================================================================
# 1. LOGGING SETUP
# =================================================================================
log_formatter = logging.Formatter('%(asctime)s - [%(threadName)s] - %(levelname)s - %(message)s')

file_handler = RotatingFileHandler('dnc_system.log', maxBytes=1000000, backupCount=5)
file_handler.setFormatter(log_formatter)

console_handler = logging.StreamHandler()
console_handler.setFormatter(log_formatter)

logger = logging.getLogger()
logger.setLevel(logging.INFO)
logger.addHandler(file_handler)
logger.addHandler(console_handler)

# =================================================================================
# 2. HELPER FUNCTIONS (DEBUG)
# =================================================================================
def hex_dump(data):
    """Converts bytes to readable HEX + ASCII string for debugging"""
    if not data: return ""
    hex_str = " ".join(f"{b:02X}" for b in data)
    # Show ASCII characters if readable, otherwise show a dot
    ascii_str = "".join(chr(b) if 32 <= b <= 126 else "." for b in data)
    return f"{hex_str} | {ascii_str}"

# =================================================================================
# 3. CNC TEMPLATES (UPDATED)
# =================================================================================
CNC_TEMPLATES = {
    "FANUC": {
        "baudrate": 4800, "bytesize": 7, "parity": "E", "stopbits": 2, 
        "xonxoff": True, "rtscts": False, "encoding": "ascii",
        "start_trigger": b'%', "end_trigger": b'%', 
        "min_content_length": 3, "rx_timeout": 5,
        "filename_pattern": r"([O:]\d{4})", "request_pattern": r"/R/(O\d{4})",
        "leader_nulls": 20, "trailer_nulls": 20,
        "eol": b'\r\n'  # <--- NEW: Fanuc often requires CR+LF
    },
    "HEIDENHAIN": {
        "baudrate": 9600, "bytesize": 7, "parity": "E", "stopbits": 1, 
        "xonxoff": True, "rtscts": False, "encoding": "ascii",
        "start_trigger": b'BEGIN PGM', "end_trigger": b'END PGM',
        "min_content_length": 10, "rx_timeout": 5,
        "filename_pattern": r"BEGIN PGM ([A-Z0-9_]+)", "request_pattern": r"CALL PGM ([A-Z0-9_]+)",
        "leader_nulls": 0, "trailer_nulls": 0,
        "eol": b'\r\n'  # <--- NEW
    },
    "SIEMENS": {
        "baudrate": 9600, "bytesize": 8, "parity": "N", "stopbits": 1, 
        "xonxoff": True, "rtscts": False, "encoding": "latin-1",
        "start_trigger": b'%', "end_trigger": b'%', 
        "min_content_length": 3, "rx_timeout": 5,
        "filename_pattern": r"_N_([A-Z0-9_]+)_MPF", "request_pattern": r"/R/([A-Z0-9_]+)",
        "leader_nulls": 0, "trailer_nulls": 0,
        "eol": b'\n'    # <--- NEW: Siemens (Linux based) often OK with just LF
    },
    # ... (Update other templates accordingly)
    "DEFAULT": {
        "baudrate": 9600, "bytesize": 8, "parity": "N", "stopbits": 1,
        "xonxoff": False, "rtscts": False, "encoding": "ascii",
        "start_trigger": b'%', "end_trigger": b'%',
        "min_content_length": 3, "rx_timeout": 5,
        "filename_pattern": None, "request_pattern": None,
        "leader_nulls": 0, "trailer_nulls": 0,
        "eol": b'\r\n'
    }
}

# =================================================================================
# 4. DNC PORT CLASS
# =================================================================================
class DNCPort(threading.Thread):
    def __init__(self, port_id, config_file, library_path="./data/library"):
        super().__init__()
        self.port_id = port_id      
        self.name = port_id         
        self.config_file = config_file
        self.library_path = library_path
        
        self.running = True
        self.abort_flag = False
        self.monitor = False  # <--- NEW: Controls if we show live debug data
        
        self.ser = None
        self.buffer = bytearray()
        self.is_receiving = False
        self.last_rx_time = 0
        self.last_open_error_log = 0
        
        self.config = self.load_config()
        
        self.base_dir = f"./data/{self.port_id}"
        self.path_in = os.path.join(self.base_dir, "in")
        self.path_out = os.path.join(self.base_dir, "out")
        self.path_sent = os.path.join(self.base_dir, "sent")
        self.path_aborted = os.path.join(self.base_dir, "aborted")
        self.ensure_directories()

    def ensure_directories(self):
        for p in [self.path_in, self.path_out, self.path_sent, self.path_aborted, self.library_path]:
            os.makedirs(p, exist_ok=True)

    def load_config(self):
        final_config = CNC_TEMPLATES["DEFAULT"].copy()
        
        # Mapping from text (JSON) to bytes (Serial)
        eol_map = {
            "LF": b'\n',
            "CR": b'\r',
            "CRLF": b'\r\n'
        }

        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, 'r') as f:
                    user_config = json.load(f)
                
                # 1. Handle template selection
                template_name = user_config.get("template", "FANUC")
                if template_name in CNC_TEMPLATES:
                    final_config = CNC_TEMPLATES[template_name].copy()
                    logging.info(f"[{self.port_id}] Loading template: {template_name}")
                else:
                    logging.warning(f"[{self.port_id}] Unknown template: {template_name}. Using DEFAULT.")
                
                # 2. Translate EOL from string to bytes, if present in JSON
                if "eol" in user_config:
                    eol_str = user_config["eol"]
                    if eol_str in eol_map:
                        user_config["eol"] = eol_map[eol_str]
                    else:
                        logging.warning(f"[{self.port_id}] Unknown EOL type: {eol_str}. Using default.")
                        # Remove the invalid string so it doesn't overwrite the template
                        del user_config["eol"]

                # 2b. Triggers from JSON are text - the serial data is bytes
                for key in ("start_trigger", "end_trigger"):
                    if isinstance(user_config.get(key), str):
                        user_config[key] = user_config[key].encode("latin-1")

                # 3. Merge user config into the template
                final_config.update(user_config)
                
            except Exception as e:
                logging.error(f"[{self.port_id}] Config error: {e}")
        else:
            logging.warning(f"[{self.port_id}] No config file found!")
            
        return final_config
        
        
    def open_serial(self):
        try:
            parity_map = {'N': serial.PARITY_NONE, 'E': serial.PARITY_EVEN, 'O': serial.PARITY_ODD}
            device_name = self.config.get("port", "/dev/ttyUSB0") 
  
            self.ser = serial.serial_for_url(
              device_name,
              baudrate=self.config.get("baudrate", 9600),
              bytesize=self.config.get("bytesize", 8),
              parity=parity_map.get(self.config.get("parity", "N"), serial.PARITY_NONE),
              stopbits=self.config.get("stopbits", 1),
              xonxoff=self.config.get("xonxoff", False),
              rtscts=self.config.get("rtscts", False),
              timeout=0.05,
              write_timeout=2.0,
              do_not_open=False
            )
                      
            logging.info(f"Port opened: {device_name} @ {self.config.get('baudrate')}")
            self.last_open_error_log = 0
            return True
        except Exception as e:
            now = time.time()
            if now - self.last_open_error_log >= 60:
                logging.error(f"COULD NOT OPEN PORT ({self.config.get('port')}): {e}")
                self.last_open_error_log = now
            return False

    def abort_job(self):
        logging.warning(f"[{self.port_id}] STOP COMMAND RECEIVED!")
        self.abort_flag = True
        if self.ser and self.ser.is_open:
            try:
                self.ser.reset_output_buffer()
            except Exception:
                pass  # not supported on all port types (e.g. socket://)

    def send_program(self, filepath):
        filename = os.path.basename(filepath)
        logging.info(f"Starting sending of: {filename}")
        self.abort_flag = False
        
        leader_count = self.config.get("leader_nulls", 0)
        trailer_count = self.config.get("trailer_nulls", 0)
        target_eol = self.config.get("eol", b'\r\n')     # Get desired EOL
        encoding = self.config.get("encoding", "ascii") # Get encoding
        
        try:
            # 1. PREPARE DATA (CONVERT NEWLINES)
            # We read the whole file as binary first to avoid encoding errors during reading
            with open(filepath, 'rb') as f:
                raw_data = f.read()

            try:
                # Decode to string to manipulate newlines
                text_data = raw_data.decode(encoding, errors='ignore')
                
                # Normalize ALL newlines to \n first (removes existing \r)
                text_data = text_data.replace('\r\n', '\n').replace('\r', '\n')
                
                # Now convert \n to machine's desired EOL (e.g. \r\n)
                
                # Method: Split on \n and rejoin with target bytes
                lines = text_data.split('\n')
                
                # Encode each line back to bytes and add target EOL
                # Note: 'filter' removes empty lines if split creates one at the end,
                # but preserving structure is usually safer.
                
                encoded_chunks = []
                for line in lines:
                     # Avoid sending EOL on the very last empty line if file ends with newline
                    encoded_line = line.encode(encoding, errors='replace')
                    encoded_chunks.append(encoded_line)

                # Join everything with the correct separator
                final_data = target_eol.join(encoded_chunks)
                
            except Exception as e:
                logging.error(f"Error during EOL conversion: {e}. Sending RAW data.")
                final_data = raw_data

            # 2. SEND LEADER
            if leader_count > 0:
                self.ser.write(b'\x00' * leader_count)
                self.ser.flush()

            # 3. SEND DATA IN CHUNKS
            chunk_size = 64
            total_len = len(final_data)
            offset = 0

            while offset < total_len:
                if self.abort_flag: raise Exception("Manual STOP activated")
                
                # Take a chunk of our converted data
                chunk = final_data[offset:offset+chunk_size]
                offset += chunk_size
                
                try:
                    self.ser.write(chunk)
                    self.ser.flush()
                    
                    if self.monitor:
                        # Only show a bit of data to avoid spamming the log
                        print(f"[{self.port_id} TX] -> {len(chunk)} bytes")
                        
                except serial.SerialTimeoutException:
                    raise Exception("Write timeout! (Check Flow Control)")
            
            # 4. SEND TRAILER
            if trailer_count > 0:
                self.ser.write(b'\x00' * trailer_count)
                self.ser.flush()

            logging.info(f"Sending finished: {filename}")
            
            # Move file to 'sent' folder
            try:
                shutil.move(filepath, os.path.join(self.path_sent, filename))
            except shutil.Error:
                os.remove(filepath) 
            
        except Exception as e:
            logging.error(f"Sending aborted ({filename}): {e}")
            try:
                if os.path.exists(filepath):
                    shutil.move(filepath, os.path.join(self.path_aborted, filename))
            except: pass
        
        finally:
            time.sleep(1)
            
    def process_received_data(self, data_bytes):
        try:
            encoding = self.config.get("encoding", "ascii")
            content = data_bytes.decode(encoding, errors='ignore')
            
            req_pattern = self.config.get("request_pattern")
            if req_pattern:
                match = re.search(req_pattern, content, re.IGNORECASE)
                if match:
                    prog = match.group(1)
                    logging.info(f"REMOTE REQUEST RECEIVED: {prog}")
                    self.queue_program_for_sending(prog)
                    return 

            name_pattern = self.config.get("filename_pattern")
            filename = "unknown.nc"
            if name_pattern:
                match = re.search(name_pattern, content)
                if match:
                    filename = f"{self.clean_program_name(match.group(1))}.nc"
            
            if filename == "unknown.nc":
                filename = f"rec_{time.strftime('%Y%m%d_%H%M%S')}.nc"

            save_path = os.path.join(self.path_in, filename)
            if os.path.exists(save_path):
                timestamp = time.strftime("%Y%m%d_%H%M%S")
                base, ext = os.path.splitext(filename)
                filename = f"{base}_{timestamp}{ext}"
                save_path = os.path.join(self.path_in, filename)

            with open(save_path, 'wb') as f:
                f.write(data_bytes)
            logging.info(f"Program received and saved: {filename}")

        except Exception as e:
            logging.error(f"Error in data processing: {e}")

    @staticmethod
    def clean_program_name(name):
        """Fanuc ':1236' -> 'O1236'. Removes characters not allowed in filenames."""
        name = name.strip()
        if name.startswith(":"):
            name = "O" + name[1:]
        return re.sub(r'[^A-Za-z0-9_.-]', '_', name)

    def queue_program_for_sending(self, program_name):
        program_name = self.clean_program_name(program_name)
        if not program_name.endswith(".nc") and not program_name.endswith(".txt"):
            filename = program_name + ".nc"
        else:
            filename = program_name

        src = os.path.join(self.library_path, filename)
        dst = os.path.join(self.path_out, filename)

        if not os.path.exists(src):
            # e.g. 'o1236' typed, 'O1236.nc' in the library
            matches = [f for f in os.listdir(self.library_path) if f.lower() == filename.lower()]
            if matches:
                filename = matches[0]
                src = os.path.join(self.library_path, filename)
                dst = os.path.join(self.path_out, filename)

        if os.path.exists(src):
            # Copy to a temp name first, so the send loop never sees a half-written file
            tmp = os.path.join(self.base_dir, filename + ".tmp")
            shutil.copy(src, tmp)
            os.replace(tmp, dst)
            logging.info(f"[{self.port_id}] Program {filename} queued.")
            return True
        else:
            logging.warning(f"[{self.port_id}] The program {filename} does NOT exist in the library!")
            return False

    def handle_timeout(self):
        logging.warning(f"RX TIMEOUT ({self.config.get('rx_timeout')}s) - Aborting.")
        if len(self.buffer) > 0:
            ts = time.strftime("%Y%m%d_%H%M%S")
            fname = f"error_timeout_{ts}.partial"
            with open(os.path.join(self.path_in, fname), 'wb') as f:
                f.write(self.buffer)
        self.buffer = bytearray()
        self.is_receiving = False

    def listen_smart(self):
        # 1. TIMEOUT CHECK
        if self.is_receiving:
            limit = self.config.get("rx_timeout", 5)
            if (time.time() - self.last_rx_time) > limit:
                self.handle_timeout()

        # 2. READ DATA
        if self.ser.in_waiting > 0:
            try:
                chunk = self.ser.read(self.ser.in_waiting)
                self.last_rx_time = time.time()
                
                # --- NEW DEBUG MONITOR START ---
                if self.monitor:
                    # Show raw data directly in terminal (HEX + ASCII)
                    debug_info = hex_dump(chunk)
                    print(f"\n[DEBUG {self.port_id}] RX ({len(chunk)}b): {debug_info}")
                # --- NEW DEBUG MONITOR END ---

                start_trig = self.config.get("start_trigger", b'%')
                end_trig = self.config.get("end_trigger", b'%')
                min_len = self.config.get("min_content_length", 3)

                if not self.is_receiving:
                    if start_trig in chunk:
                        self.is_receiving = True
                        idx = chunk.find(start_trig)
                        self.buffer = bytearray(chunk[idx:])
                        logging.info("Start receiving...")
                else:
                    self.buffer.extend(chunk)

                if self.is_receiving:
                    content_len = len(self.buffer) - len(start_trig)
                    if content_len >= min_len:
                        content_part = self.buffer[len(start_trig):]
                        if end_trig in content_part:
                            logging.info("End receiving (OK).")
                            end_pos = content_part.find(end_trig)
                            total_len = len(start_trig) + end_pos + len(end_trig)
                            final_data = self.buffer[:total_len]
                            self.process_received_data(final_data)
                            self.buffer = bytearray()
                            self.is_receiving = False
            
            except Exception as e:
                logging.error(f"Read error: {e}")
                if self.monitor:
                    print(f"[!!! ERROR {self.port_id}]: {e}")
                self.buffer = bytearray()
                self.is_receiving = False
                # Lost connection (e.g. Moxa socket disconnected): let run() close and reconnect
                if isinstance(e, (OSError, serial.SerialException)):
                    raise

    def run(self):
        logging.info(f"[{self.port_id}] Thread active. Monitoring {self.path_out}")
        
        while self.running:
            # 1. CHECK PORT CONNECTION
            if self.ser is None or not self.ser.is_open:
                if not self.open_serial():
                    try:
                        waiting_files = [f for f in os.listdir(self.path_out) if os.path.isfile(os.path.join(self.path_out, f))]
                        if waiting_files and int(time.time()) % 10 == 0:
                            logging.warning(f"[{self.port_id}] Files queued, but PORT CLOSED!")
                    except: pass
                    
                    time.sleep(5)
                    continue 

            # 2. IF PORT IS OPEN: RUN NORMALLY
            try:
                # Check for files in OUT folder
                files = [f for f in os.listdir(self.path_out) 
                         if os.path.isfile(os.path.join(self.path_out, f))]
                
                if files:
                    files.sort(key=lambda x: os.path.getmtime(os.path.join(self.path_out, x)))
                    filename = files[0]
                    target = os.path.join(self.path_out, filename)
                    
                    age = time.time() - os.path.getmtime(target)
                    size = os.path.getsize(target)
                    if size > 0 and age >= 2:
                        # Age check: a file copied in by hand may still be being written
                        self.send_program(target)
                        time.sleep(1) 
                    elif size == 0 and age > 10:
                        # Empty file: would otherwise block the queue (and receiving) forever
                        logging.warning(f"Empty file in out-folder moved to aborted: {filename}")
                        shutil.move(target, os.path.join(self.path_aborted, filename))
                    else:
                        self.listen_smart()
                else:
                    # No files to send -> Listen for incoming
                    self.listen_smart()
                
                time.sleep(0.02) 
                
            except (OSError, serial.SerialException) as e:
                logging.error(f"Hardware error (Cable unplugged?): {e}")
                if self.ser: 
                    try: self.ser.close() 
                    except: pass
                self.ser = None
                self.buffer = bytearray()
                self.is_receiving = False
                time.sleep(2)

            except Exception as e:
                logging.error(f"General loop error: {e}")
                time.sleep(2)

        if self.ser and self.ser.is_open:
            self.ser.close()
            logging.info("Port thread finished.")

# =================================================================================
# 5. MAIN PROGRAM (MENU / SERVICE)
# =================================================================================
if __name__ == "__main__":
    import sys

    if not os.path.exists("./configs"): os.makedirs("./configs")
    if not os.path.exists("./data/library"): os.makedirs("./data/library")

    print("\n" + "="*40)
    print("   PYTHON MULTI-DNC SERVER v2.1 (DEBUG)")
    print("="*40)
    
    threads = {}
    config_files = [f for f in os.listdir("./configs") if f.endswith(".json")]
    
    if not config_files:
        print("WARNING: No files in /configs! Create e.g. test.json")
    
    for cfg_file in config_files:
        port_id = cfg_file.replace(".json", "") 
        config_path = os.path.join("./configs", cfg_file)
        
        dnc_thread = DNCPort(port_id, config_path)
        dnc_thread.daemon = True
        dnc_thread.start()
        
        threads[port_id] = dnc_thread
        logging.info(f"Starting thread: {port_id}")

    # Check if we are running in a terminal (manual) or as a service (systemd)
    if sys.stdin and sys.stdin.isatty():
        # MANUAL MODE: Show menu
        print("\nCommands:")
        print("  send [port] [program]  -> Send program")
        print("  monitor [port]         -> Toggle live data debug")
        print("  status                 -> View status")
        print("  stop [port]            -> Abort current job")
        print("  exit                   -> Shut everything down")
        
        try:
            while True:
                cmd_input = input("DNC> ").strip().split()
                if not cmd_input: continue
                
                action = cmd_input[0].lower()

                if action == "exit":
                    print("Shutting down...")
                    for t in threads.values(): t.running = False
                    break
                
                elif action == "status":
                    print(f"--- STATUS ({len(threads)} ports) ---")
                    for pid, t in threads.items():
                        status = "Running" if t.is_alive() else "Dead"
                        port_info = "Open" if (t.ser and t.ser.is_open) else "CLOSED"
                        mon_state = "DEBUG ON" if t.monitor else ""
                        print(f" {pid:<10} | Thread: {status} | Port: {port_info} {mon_state}")
                
                elif action == "monitor":
                    if len(cmd_input) < 2:
                        print("Error: Specify port name. E.g: monitor fanuc")
                    else:
                        port = cmd_input[1]
                        if port in threads:
                            t = threads[port]
                            t.monitor = not t.monitor
                            state = "ON" if t.monitor else "OFF"
                            print(f"--- MONITOR {state} FOR {port} ---")
                            if t.monitor:
                                print("INFO: Raw data is now shown in HEX and ASCII.")
                                print("      Press ENTER to retrieve prompt (data will still print).")
                        else:
                            print("Unknown port.")

                elif action == "send":
                    if len(cmd_input) < 3:
                        print("Usage: send [port] [program] (e.g: send v_test O1234)")
                    else:
                        port, prog = cmd_input[1], cmd_input[2]
                        if port in threads:
                            threads[port].queue_program_for_sending(prog)
                        else:
                            print("Unknown port.")

                elif action == "stop":
                    if len(cmd_input) < 2:
                        print("Usage: stop [port]")
                    else:
                        port = cmd_input[1]
                        if port in threads:
                            print(f"Sending STOP to {port}...")
                            threads[port].abort_job()
                        else:
                            print("Unknown port.")

                else:
                    print(f"Unknown command: {action}")
        except KeyboardInterrupt:
            print("\nForced shutdown.")

    else:
        # SERVICE MODE
        logging.info("Running as Systemd Service. Menu deactivated.")
        try:
            while True:
                time.sleep(60) 
        except KeyboardInterrupt:
            logging.info("Service stopping...")
            for t in threads.values(): t.running = False
            
            
