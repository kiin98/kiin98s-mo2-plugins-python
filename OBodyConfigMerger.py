import json
import os
import re
from collections import OrderedDict

import mobase
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QMainWindow, QVBoxLayout, QComboBox, QDialog, QDialogButtonBox
from mobase.widgets import TaskDialog

PLUGIN_PATTERN = r"""^(?!.*(PRN|AUX|NUL|CO(N|M[0-9¹²³])|LPT[0-9¹²³]|[<>:"/\|?*]))(?=\S)(?=.+\S\.es[plm]$).*"""
FORM_ID_PATTERN = r'(?i)\b(?:[0-9a-f]{1,8})\b'
PLUGIN_PATTERN_2 = r'^(.+)(\.(?i:esp|esm|esl))$'


class SelectDestinationDialog(QDialog):
    def __init__(self, active_mods, callback, parent):
        super().__init__(parent)
        self.active_mods = active_mods
        self.callback = callback
        self.init_ui()

    def init_ui(self):
        self.setWindowTitle('Select Destination')

        layout = QVBoxLayout()

        self.combo_box = QComboBox()
        self.combo_box.addItem("<Overwrite Directory>")
        self.combo_box.addItem("<Create new mod: OBody Config Overwrite>")
        self.combo_box.addItems(self.active_mods)
        layout.addWidget(self.combo_box)

        self.buttonBox = QDialogButtonBox()
        self.buttonBox.setStandardButtons(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttonBox.accepted.connect(self.accept)
        self.buttonBox.rejected.connect(self.reject)
        layout.addWidget(self.buttonBox)

        self.setLayout(layout)

    def accept(self):
        selected_mod = self.combo_box.currentText()
        self.callback(selected_mod)
        super().accept()


class OBodyConfigMerger(mobase.IPluginTool):
    _organizer: mobase.IOrganizer

    def __init__(self):
        super().__init__()
        self._parentWidget = None
        self.details = []
        self.new_mod_name = "OBody Config Overwrite"

    def init(self, organizer: mobase.IOrganizer) -> bool:
        self._organizer = organizer
        self._organizer.onUserInterfaceInitialized(self._ouiiCallback)
        return True

    def name(self):
        return "OBodyConfigMerger"

    def author(self):
        return "kiin98"

    def description(self):
        return "A plugin to find and combine OBody configs within all active mods."

    def version(self):
        return mobase.VersionInfo(1, 3, 3, mobase.ReleaseType.BETA)

    def settings(self):
        return []

    def icon(self):
        return QIcon()

    def displayName(self):
        return "OBodyConfigMerger"

    def tooltip(self):
        return "A plugin to find and combine OBody configs within all active mods."

    def display(self):
        active_mods = self.list_active_mods()

        def on_destination_selected(selected_option):
            select_mod_dialog.close()
            folder_name = os.path.join("SKSE", "Plugins")
            filename_to_find = "OBody_presetDistributionConfig.json"

            selected_mod_name = None
            if selected_option == "<Overwrite Directory>":
                output_path = os.path.join(self._organizer.overwritePath(), "SKSE", "Plugins")
            elif selected_option == "<Create new mod: OBody Config Overwrite>":
                if self.new_mod_name not in self._organizer.modList().allModsByProfilePriority():
                    guessed_data = mobase.GuessedString(self.new_mod_name)
                    new_mod = self._organizer.createMod(guessed_data)
                output_path = os.path.join(self._organizer.modsPath(), self.new_mod_name, "SKSE", "Plugins")
                selected_mod_name = self.new_mod_name
            else:
                output_path = os.path.join(self._organizer.modsPath(), selected_option, "SKSE", "Plugins")
                selected_mod_name = selected_option

            combined_data = self.find_and_combine_json_files(folder_name, filename_to_find, selected_mod_name)

            output_file = os.path.join(output_path, filename_to_find)
            os.makedirs(output_path, exist_ok=True)

            self.save_combined_json(combined_data, output_file)
            self.log("\n\n")
            self.log(f"Combined JSON data saved to:\n'{output_file}'")
            self.show_popup_done()
            self._organizer.refresh()

        select_mod_dialog = SelectDestinationDialog(active_mods, on_destination_selected, self._parentWidget)
        select_mod_dialog.show()

    def log(self, msg):
        print("OBodyConfigMerger::log:", msg)
        self.details.append(msg)

    def show_popup_done(self):
        dialog = TaskDialog(title="OBodyConfigMerger Finished", main="Successfully merged all configs!")
        dialog.setContent(self.details[-1])
        dialog.setDetails("\n".join(self.details))
        dialog.exec()

    def _ouiiCallback(self, main_window: "QMainWindow"):
        self._parentWidget = main_window

    def list_active_mods(self):
        modlist = self._organizer.modList()
        active_mods = [mod for mod in modlist.allModsByProfilePriority() if modlist.state(mod) & mobase.ModState.ACTIVE]
        return active_mods

    def find_and_combine_json_files(self, folder_name, filename, selected_mod_name):
        active_mods = self.list_active_mods()
        combined_data = OrderedDict()
        for mod in active_mods:
            if mod == selected_mod_name:
                continue
            folder_path = os.path.join(self._organizer.modsPath(), mod, folder_name)
            file_path = os.path.join(folder_path, filename)
            if os.path.exists(file_path):
                self.log(f"\n\nOBody config found in: {file_path}")
                with open(file_path, 'r', encoding='utf-8') as file:
                    json_data = json.load(file, object_pairs_hook=OrderedDict)
                    self.log(f"Merging {mod}...")
                    self.merge_dicts(combined_data, json_data)
        return combined_data

    def clean_value_list(self, value_list, key):
        new_value_list = []
        for item in value_list:
            item = item.strip()
            if item == "":
                self.log(f"    Removed Empty String in: {key}")
                continue
            if key in ["blacklistedNpcsPluginFemale", "blacklistedNpcsPluginMale",
                       "blacklistedOutfitsFromORefitPlugin"]:
                if not re.fullmatch(PLUGIN_PATTERN, item):
                    self.log(f"    Removed Invalid Plugin Name in {key}: {item}")
                    continue
            elif re.fullmatch(FORM_ID_PATTERN, item):
                item = self.fix_form_id(item)
            new_value_list.append(item)
        return new_value_list

    def fix_form_id(self, hex_string):
        match = re.fullmatch(FORM_ID_PATTERN, hex_string)
        new_key = match.group(0).zfill(8)
        new_key = new_key.upper()
        if new_key != hex_string:
            self.log(f"    Renamed FormID {hex_string} to {new_key}")
        return new_key

    def clean_dict(self, d):
        cleaned_dict = OrderedDict()
        for key, value in d.items():
            new_key = key
            if isinstance(key, str):
                new_key = new_key.strip()
                if new_key == "":
                    continue
                if key == "blacklistedRaces":
                    self.log(f"   Deprecated key: {key}, replaced with blacklistedRacesFemale, blacklistedRacesMale")
                    new_key = "blacklistedRacesFemale"
                    cleaned_dict["blacklistedRacesMale"] = self.clean_value_list(value, "blacklistedRacesMale")
                elif re.fullmatch(PLUGIN_PATTERN_2, key):
                    match = re.fullmatch(PLUGIN_PATTERN_2, key)
                    name = match.group(1)
                    ending = match.group(2)
                    new_key = f"{name}{ending.lower()}"
                    if new_key != key:
                        self.log(f"Setting {key} to {new_key}")
                elif re.fullmatch(FORM_ID_PATTERN, key):
                   new_key = self.fix_form_id(key)

            if isinstance(value, dict):
                cleaned_dict[new_key] = self.clean_dict(value)
            elif isinstance(value, list):
                cleaned_dict[new_key] = self.clean_value_list(value, new_key)
            else:
                cleaned_dict[new_key] = value

        return cleaned_dict

    def merge_dicts(self, dict1, dict2):
        dict2 = self.clean_dict(dict2)

        for key, value in dict2.items():
            if key in dict1:
                if isinstance(dict1[key], OrderedDict) and isinstance(value, OrderedDict):
                    self.log(f"Merging nested dictionary for key: {key}")
                    self.merge_dicts(dict1[key], value)
                elif isinstance(dict1[key], list) and isinstance(value, list):
                    self.log(f"    Extending list for key: {key}")
                    combined_list = dict1[key] + value
                    dict1[key] = list(OrderedDict.fromkeys(combined_list))
                else:
                    self.log(f"Overwriting {key}: {dict1[key]} with {dict2[key]}")
                    dict1[key] = value
            else:
                dict1[key] = value

    @staticmethod
    def save_combined_json(combined_data, output_file):
        with open(output_file, 'w', encoding='utf-8') as file:
            json.dump(combined_data, file, ensure_ascii=False, indent=4)


def createPlugin():
    return OBodyConfigMerger()
