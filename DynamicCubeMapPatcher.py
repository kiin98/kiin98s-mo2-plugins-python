import glob
import json
import os
import shutil
import subprocess
from dataclasses import asdict

import mobase
from PyQt6 import QtCore
from PyQt6.QtCore import QThread, QObject, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QMainWindow, QProgressDialog, QMessageBox

from DDS.DDSDefinitions import DDS_HEADER, DDS_MAGIC_NUMBER

def read_dds_header(file_path):
    with open(file_path, "rb") as f:
        magic = f.read(4)
        if magic != DDS_MAGIC_NUMBER:
            raise ValueError("Not a valid DDS file")
        header_bytes = f.read(124)
        header = DDS_HEADER()
        header.fromBytes(header_bytes)
        return header


def get_path_from_textures(file_path):
    parts = file_path.lower().split(os.sep)
    try:
        textures_index = parts.index('textures')
        return os.sep.join(parts[textures_index:])
    except ValueError:
        return None


def log(msg):
    print("DynamicCubeMapPatcher::log:", msg)


class CubemapWorker(QObject):
    progress = pyqtSignal(int)
    finished = pyqtSignal(dict)
    cancelled = pyqtSignal()
    missing_cubemap = pyqtSignal()
    error = pyqtSignal(str, str)

    def __init__(self, dynamic_cubemap, organizer, new_mod_name, dynamic_cubemap_json_name, colored_cubemaps):
        super().__init__()
        self.dynamic_cubemap = dynamic_cubemap
        self.organizer = organizer
        self._cancel_requested = False
        self.new_mod_name = new_mod_name
        self.colored_cubemaps = colored_cubemaps
        self.dynamic_cubemap_path = None
        self.dynamic_cubemap_json_name = dynamic_cubemap_json_name
        self.dynamic_cubemap_json_path = None

    @pyqtSlot()
    def process(self):
        cubemaps = {}
        active_mods = self.list_active_mods()
        dds_files = []


        for mod in active_mods:
            mod_path = os.path.join(self.organizer.modsPath(), mod)
            if os.path.exists(os.path.join(mod_path, self.dynamic_cubemap)):
                self.dynamic_cubemap_path = os.path.join(mod_path, self.dynamic_cubemap)
            if os.path.exists(os.path.join(mod_path, self.dynamic_cubemap_json_name)):
                self.dynamic_cubemap_json_path = os.path.join(mod_path, self.dynamic_cubemap_json_name)
            dds_files.extend(glob.glob(os.path.join(mod_path, '**', "*.dds"), recursive=True))

        if not self.dynamic_cubemap_path or not self.dynamic_cubemap_json_path:
            self.missing_cubemap.emit()
            self.cancelled.emit()
            return

        try:
            with open(self.dynamic_cubemap_json_path, "r") as f:
                black_lists = json.load(f)
                files_black_list = black_lists.get("FILES_BLACK_LIST", [])
                folder_black_list = black_lists.get("FOLDER_BLACK_LIST", [])
                log(f"Loaded files black lists: {files_black_list} and folder black lists: {folder_black_list}")
        except Exception as e:
            log(f"Encountered error while loading json file: {e}")
            self.error.emit(f"{e}", f"Check the {self.dynamic_cubemap_json_name} file")
            self.cancelled.emit()
            return


        dds_files = [f for f in dds_files if f.lower().endswith('.dds')]

        total_dds_files = len(dds_files)
        processed_files = 0

        for file_path in dds_files:
            if self._cancel_requested:
                self.cancelled.emit()
                return
            try:
                header = read_dds_header(file_path)
            except Exception as e:
                log(f"Error reading {file_path}: {e}")
                continue
            if header.dwCaps2 & DDS_HEADER.Caps2.DDSCAPS2_CUBEMAP:
                log(f"Found cubemap:{file_path} with header:\n{asdict(header)}")
                textures_path = get_path_from_textures(file_path)
                filename = os.path.basename(file_path).lower()
                folders = os.path.dirname(file_path).split(os.sep)
                if not textures_path:
                    log(f"Skipping cubemap not in textures: {file_path}")
                elif filename in files_black_list or not set(folders).isdisjoint(set(folder_black_list)):
                    log(f"Skipping blacklisted cubemap: {file_path}")
                elif header.dwWidth * header.dwHeight == 1:
                    log(f"Skipping 1x1 cubemap: {file_path}")
                    output_path = os.path.join(
                        self.organizer.modsPath(),
                        self.new_mod_name,
                        textures_path
                    )
                    if os.path.exists(output_path):
                        os.remove(output_path)
                else:
                    if self.colored_cubemaps:
                        self.convert_cubemap_to_1x1(file_path)
                    else:
                        self.copy_cubemap(textures_path)

            processed_files += 1
            progress = int((processed_files / total_dds_files) * 100)
            self.progress.emit(progress)

        self.progress.emit(100)
        self.finished.emit(cubemaps)

    def list_active_mods(self):
        modlist = self.organizer.modList()
        return [mod for mod in modlist.allModsByProfilePriority() if modlist.state(mod) & mobase.ModState.ACTIVE]

    def copy_cubemap(self, cubemap_texture_path):
        output_path = os.path.join(
            self.organizer.modsPath(),
            self.new_mod_name,
            cubemap_texture_path
        )
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        try:
            shutil.copy2(self.dynamic_cubemap_path, output_path)
        except Exception as e:
            log(f"Error copying {self.dynamic_cubemap_path}: {e}")
            return

        log(f"Copied {self.dynamic_cubemap_path} to {output_path}")

    def convert_cubemap_to_1x1(self, cubemap_path):
        output_path = os.path.join(
            self.organizer.modsPath(),
            self.new_mod_name,
            os.path.dirname(get_path_from_textures(cubemap_path))
        )
        os.makedirs(output_path, exist_ok=True)

        result = subprocess.run([
            os.path.join(self.organizer.getPluginDataPath(), "texconv.exe"),
            cubemap_path,
            "-o", output_path,
            "-f", "R8G8B8A8_UNORM",
            "-ft", "dds", "-y",
            "-w", "1", "-h", "1", "-m", "1"
        ], capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)

        if result.returncode != 0:
            log(f"Error converting {cubemap_path} to 1x1: {result.stderr.decode()}")
            return

        log(f"Generated {cubemap_path} at {output_path}")

    def request_cancel(self):
        self._cancel_requested = True


class DynamicCubeMapPatcher(mobase.IPluginTool):
    _organizer: mobase.IOrganizer

    def __init__(self):
        super().__init__()
        self.new_mod_name = "Dynamic Cubemaps Overwrite"
        self.dynamic_cubemap = os.path.join("Textures", "cubemaps", "dynamic1pxcubemap_black.dds")
        self.dynamic_cubemap_exists = None
        self.dynamic_cubemap_mod_name = None
        self.dynamic_cubemap_json_name = "DynamicCubemap_Blacklist.json"
        self.progress_dialog = None
        self._parentWidget = None
        self.worker = None
        self.worker_thread = None
        self.details = []

    def init(self, organizer: mobase.IOrganizer) -> bool:
        self._organizer = organizer
        self._organizer.onUserInterfaceInitialized(self._ouiiCallback)
        return True

    def name(self):
        return "DynamicCubeMapPatcher"

    def author(self):
        return "kiin98"

    def description(self):
        return "A plugin to find all cubemaps and convert them to dynamic cubemaps"

    def version(self):
        return mobase.VersionInfo(2, 1, 0, mobase.ReleaseType.BETA)

    def settings(self):
        return [
            mobase.PluginSetting(
                "colored cubemaps",
                "Enable experimental colored cubemaps for Community Shaders averaged over all pixels. Set to false for ENB",
                False
            )
        ]

    def icon(self):
        return QIcon()

    def displayName(self):
        return "DynamicCubeMapPatcher"

    def tooltip(self):
        return "A plugin to find all cubemaps and convert them to dynamic cubemaps"

    def display(self):
        guessed_data = mobase.GuessedString(self.new_mod_name)
        new_mod = self._organizer.createMod(guessed_data)

        if new_mod is None:
            log("Process canceled: Mod could not be created")
            return

        self.new_mod_name = new_mod.name()

        self.progress_dialog = QProgressDialog("Scanning and copying cubemaps...", "Cancel", 0, 100, self._parentWidget)
        self.progress_dialog.setWindowTitle("Processing")
        self.progress_dialog.setWindowModality(QtCore.Qt.WindowModality.WindowModal)
        self.progress_dialog.canceled.connect(self.cancel_processing)
        self.progress_dialog.show()

        self.worker = CubemapWorker(
            self.dynamic_cubemap, self._organizer, self.new_mod_name, self.dynamic_cubemap_json_name,
            self._organizer.pluginSetting(self.name(), "colored cubemaps"),
        )
        self.worker_thread = QThread()

        self.worker.moveToThread(self.worker_thread)
        self.worker_thread.started.connect(self.worker.process)
        self.worker.finished.connect(self.worker_thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)

        self.worker.progress.connect(self.update_progress)
        self.worker.cancelled.connect(self.on_worker_cancelled)
        self.worker.missing_cubemap.connect(self.show_missing_cubemap_warning)
        self.worker.error.connect(self.show_error)
        self.worker_thread.finished.connect(self.worker_thread.deleteLater)
        self.worker_thread.start()

    def update_progress(self, progress):
        if self.progress_dialog is not None:
            self.progress_dialog.setValue(progress)
            if self.progress_dialog.wasCanceled():
                self.cancel_processing()
        if progress == 100:
            self._organizer.refresh()

    def cancel_processing(self):
        if self.worker_thread.isRunning():
            self.worker.request_cancel()
            self.worker_thread.quit()
            self.worker_thread.wait()
        if self.progress_dialog is not None:
            self.progress_dialog.close()
        log("Processing cancelled")

    def on_worker_cancelled(self):
        self.cancel_processing()

    def show_missing_cubemap_warning(self):
        msg = QMessageBox()
        msg.setIcon(QMessageBox.Icon.Warning)
        msg.setText(f"Could not find the dynamic cube map file: {self.dynamic_cubemap} or settings file {self.dynamic_cubemap_json_name}!")
        msg.setInformativeText("Make sure you installed the dynamic cubemap and json from the files section!")
        msg.setWindowTitle("Path Not Found")
        msg.exec()

    def show_error(self, message, info):
        msg = QMessageBox()
        msg.setIcon(QMessageBox.Icon.Warning)
        msg.setText(message)
        msg.setInformativeText(info)
        msg.setWindowTitle("Error")
        msg.exec()

    def _ouiiCallback(self, main_window: "QMainWindow"):
        self._parentWidget = main_window

    def list_active_mods(self):
        modlist = self._organizer.modList()
        return [mod for mod in modlist.allModsByProfilePriority() if modlist.state(mod) & mobase.ModState.ACTIVE]


def createPlugin():
    return DynamicCubeMapPatcher()
