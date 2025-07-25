import sys
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
                             QLabel, QLineEdit, QPushButton, QTextEdit, QFileDialog,
                             QProgressBar, QMessageBox, QCheckBox, QGroupBox, QTreeWidget,
                             QTreeWidgetItem, QHeaderView, QComboBox, QDialog, QTableWidget,
                             QTableWidgetItem)
from PyQt5.QtCore import QThread, pyqtSignal, Qt
from bs4 import BeautifulSoup, NavigableString
from deep_translator import GoogleTranslator
import time
import re
import os
from html import unescape


class PreviewDialog(QDialog):
    def __init__(self, samples):
        super().__init__()
        self.setWindowTitle("Translation Preview")
        self.setGeometry(200, 200, 800, 400)

    
        layout = QVBoxLayout()
        self.table = QTableWidget()
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels(["Field", "Original", "Translated"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)

        self.table.setRowCount(len(samples))
        for row, (field, original, translated) in enumerate(samples):
            self.table.setItem(row, 0, QTableWidgetItem(field))
            self.table.setItem(row, 1, QTableWidgetItem(original))
            self.table.setItem(row, 2, QTableWidgetItem(translated))

    
        layout.addWidget(self.table)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)
        layout.addWidget(close_btn)
        self.setLayout(layout)
        
class FieldMapper(QThread):
    fields_detected = pyqtSignal(list)
    
    def __init__(self, file_path):
        super().__init__()
        self.file_path = file_path
        
    def run(self):
        try:
            if not os.path.exists(self.file_path):
                self.fields_detected.emit([])
                return
                
            with open(self.file_path, 'r', encoding='utf-8') as f:
                soup = BeautifulSoup(f, 'lxml-xml')
                
            sample_product = soup.find('product')
            if not sample_product:
                self.fields_detected.emit([])
                return
                
            fields = []
            # Standard fields
            for child in sample_product.children:
                if child.name and child.name not in ['[document]', 'product']:
                    fields.append({
                        'name': child.name,
                        'path': f"/product/{child.name}",
                        'sample': str(child.string)[:50] + ("..." if len(str(child.string)) > 50 else "")
                    })
                    
            # Nested fields
            for tag in sample_product.find_all(True):
                if tag.name == 'category':
                    fields.append({
                        'name': 'category',
                        'path': f"//category",
                        'sample': str(tag.string)[:50] + ("..." if len(str(tag.string)) > 50 else "")
                    })
                elif tag.name == 'attribute':
                    attr_name = tag.find('name')
                    if attr_name and attr_name.string:
                        fields.append({
                            'name': f"attribute/{attr_name.string}",
                            'path': f"//attribute[name='{attr_name.string}']/label",
                            'sample': tag.find('label').string[:50] if tag.find('label') else ""
                        })
            
            self.fields_detected.emit(fields)
            
        except Exception as e:
            print(f"Field detection error: {str(e)}")
            self.fields_detected.emit([])

class TranslationWorker(QThread):
    progress = pyqtSignal(int, int, str)
    finished = pyqtSignal(bool, str)
    field_progress = pyqtSignal(str)
    sample_ready = pyqtSignal(list)  # For preview samples
    paused = pyqtSignal()

    def __init__(self, input_file, output_file, field_mapping, source_lang, target_lang):
        super().__init__()
        self.input_file = input_file
        self.output_file = output_file
        self.field_mapping = field_mapping
        self.source_lang = source_lang
        self.target_lang = target_lang
        self._is_running = True
        self._is_paused = False
        self.samples = []

    def stop(self):
        self._is_running = False
        
    def pause(self):
        self._is_paused = True
        self.paused.emit()
        
    def resume(self):
        self._is_paused = False

    def translate_text(self, text):
        if not text or not text.strip():
            return text
            
        try:
            if re.match(r'^[A-Z0-9\s\-_\.\/]+$', text.strip()):
                return text
                
            return GoogleTranslator(source=self.source_lang, target=self.target_lang).translate(text) 
        except Exception as e:
            print(f"Field detection error: {str(e)}")
            return text

    def get_field_content(self, product, field):
        if field['path'].startswith('/product/'):
            tag_name = field['path'].split('/')[-1]
            tag = product.find(tag_name)
            return str(tag.string) if tag and tag.string else None
        elif field['path'].startswith('//category'):
            category = product.find('category')
            return str(category.string) if category and category.string else None
        elif field['path'].startswith('//attribute'):
            attr_name = field['name'].split('/')[-1]
            attr = product.find('attribute', {'name': attr_name})
            label = attr.find('label') if attr else None
            return str(label.string) if label and label.string else None
        return None

    def run(self):
        try:
            with open(self.input_file, 'r', encoding='utf-8') as f:
                soup = BeautifulSoup(f, 'lxml-xml')

            products = soup.find_all('product')
            total = len(products)
            
            for i, product in enumerate(products):
                while self._is_paused:
                    time.sleep(0.5)
                if not self._is_running:
                    self.finished.emit(False, "Translation stopped by user")
                    return
                
                self.progress.emit(i+1, total, f"Product {i+1}/{total}")
                # Collect samples for first 5 products
                samples = []
                if i < 5:
                    for field in self.field_mapping:
                        original = self.get_field_content(product, field)
                        if original:
                            translated = self.translate_text(original)
                            samples.append((field['name'], original, translated))
                    self.sample_ready.emit(samples)
            
                # Process each selected field
                for field in self.field_mapping:
                    self.field_progress.emit(f"Translating {field['path']}")
                    
                    if field['path'].startswith('/product/'):
                        tag_name = field['path'].split('/')[-1]
                        for tag in product.find_all(tag_name):
                            if tag.string:
                                translated = self.translate_text(str(tag.string))
                                tag.string.replace_with(NavigableString(translated))
                    
                    elif field['path'].startswith('//category'):
                        for category in product.find_all('category'):
                            if category.string:
                                translated = self.translate_text(str(category.string))
                                category.string.replace_with(NavigableString(translated))
                    
                    elif field['path'].startswith('//attribute'):
                        attr_name = field['name'].split('/')[-1]
                        for attr in product.find_all('attribute'):
                            if attr.find('name') and attr.find('name').string == attr_name:
                                label = attr.find('label')
                                if label and label.string:
                                    translated = self.translate_text(str(label.string))
                                    label.string.replace_with(NavigableString(translated))
                
                time.sleep(0.3)
            
            with open(self.output_file, 'w', encoding='utf-8') as f:
                f.write(unescape(str(soup))) 
            self.finished.emit(True, f"Translated {total} products")
            
        except Exception as e:
            self.finished.emit(False, f"Error: {str(e)}")

class TranslationApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("XML Field Mapping Translator")
        self.setGeometry(100, 100, 900, 700)
        self.worker = None
        self.field_mapper = None
        self.detected_fields = []
        self.init_ui()
        
    def init_ui(self):
        main_widget = QWidget()
        layout = QVBoxLayout()
        # Language Selection
        lang_group = QGroupBox("Language Settings")
        lang_layout = QHBoxLayout()
        
        self.source_lang = QComboBox()
        self.source_lang.addItems(["auto", "en", "de", "fr", "it", "es"])
        self.target_lang = QComboBox()
        self.target_lang.addItems(["ro", "en", "de", "fr", "it", "es"])
        self.target_lang.setCurrentText("ro")
        
        lang_layout.addWidget(QLabel("Source Language:"))
        lang_layout.addWidget(self.source_lang)
        lang_layout.addWidget(QLabel("Target Language:"))
        lang_layout.addWidget(self.target_lang)
        lang_group.setLayout(lang_layout)
        
        # File Selection
        file_group = QGroupBox("File Selection")
        file_layout = QVBoxLayout()
        
        # Input File
        input_layout = QHBoxLayout()
        self.input_label = QLabel("Input XML File:")
        self.input_path = QLineEdit()
        self.input_path.textChanged.connect(self.analyze_fields)
        self.input_browse = QPushButton("Browse...")
        self.input_browse.clicked.connect(self.browse_input)
        input_layout.addWidget(self.input_label)
        input_layout.addWidget(self.input_path)
        input_layout.addWidget(self.input_browse)
        
        # Output File
        output_layout = QHBoxLayout()
        self.output_label = QLabel("Output XML File:")
        self.output_path = QLineEdit()
        self.output_browse = QPushButton("Browse...")
        self.output_browse.clicked.connect(self.browse_output)
        output_layout.addWidget(self.output_label)
        output_layout.addWidget(self.output_path)
        output_layout.addWidget(self.output_browse)
        
        file_layout.addLayout(input_layout)
        file_layout.addLayout(output_layout)
        file_group.setLayout(file_layout)
        
        # Field Mapping
        self.mapping_group = QGroupBox("Field Mapping (Double-click to edit)")
        self.mapping_group.setEnabled(False)
        mapping_layout = QVBoxLayout()
        
        self.field_tree = QTreeWidget()
        self.field_tree.setHeaderLabels(["Field Name", "XPath", "Sample Content", "Translate"])
        self.field_tree.setColumnWidth(0, 150)
        self.field_tree.setColumnWidth(1, 200)
        self.field_tree.setColumnWidth(2, 250)
        self.field_tree.header().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.field_tree.itemDoubleClicked.connect(self.edit_field_item)
        
        mapping_layout.addWidget(self.field_tree)
        self.mapping_group.setLayout(mapping_layout)
        
        # Progress
        self.progress_bar = QProgressBar()
        self.progress_label = QLabel("Ready")
        self.field_label = QLabel("Current field: None")
        
        # Buttons
        button_layout = QHBoxLayout()
        self.translate_btn = QPushButton("Start Translation")
        self.translate_btn.clicked.connect(self.start_translation)
        self.translate_btn.setEnabled(False)
        self.pause_btn = QPushButton("Pause")
        self.pause_btn.clicked.connect(self.toggle_pause)
        self.pause_btn.setEnabled(False)
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.clicked.connect(self.stop_translation)
        self.stop_btn.setEnabled(False)
        self.preview_btn = QPushButton("Show Preview")
        self.preview_btn.clicked.connect(self.show_preview)
        self.preview_btn.setEnabled(False)

        button_layout.addWidget(self.translate_btn)
        button_layout.addWidget(self.pause_btn)
        button_layout.addWidget(self.stop_btn)
        button_layout.addWidget(self.preview_btn)
        
        # Log
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        
        # Assemble layout
        layout.addWidget(lang_group)
        layout.addWidget(file_group)
        layout.addWidget(self.mapping_group)
        layout.addWidget(self.progress_bar)
        layout.addWidget(self.progress_label)
        layout.addWidget(self.field_label)
        layout.addLayout(button_layout)
        layout.addWidget(self.log)
        
        main_widget.setLayout(layout)
        self.setCentralWidget(main_widget)
        # Sample storage
        self.translation_samples = []
        
    def browse_input(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select Input XML", "", "XML Files (*.xml)")
        if path:
            self.input_path.setText(path)
            dirname, filename = os.path.split(path)
            output_path = os.path.join(dirname, f"translated_{filename}")
            self.output_path.setText(output_path)
            
    def browse_output(self):
        path, _ = QFileDialog.getSaveFileName(self, "Select Output XML", "", "XML Files (*.xml)")
        if path:
            self.output_path.setText(path)
            
    def analyze_fields(self):
        path = self.input_path.text()
        if not path or not os.path.exists(path):
            return
            
        self.mapping_group.setEnabled(False)
        self.field_tree.clear()
        self.log_message("Analyzing XML structure...")
        
        if self.field_mapper:
            self.field_mapper.terminate()
            
        self.field_mapper = FieldMapper(path)
        self.field_mapper.fields_detected.connect(self.populate_field_tree)
        self.field_mapper.start()
        
    def populate_field_tree(self, fields):
        self.detected_fields = fields
        self.field_tree.clear()
        
        if not fields:
            self.log_message("No fields detected in XML")
            return
            
        for field in fields:
            item = QTreeWidgetItem(self.field_tree)
            item.setText(0, field['name'])
            item.setText(1, field['path'])
            item.setText(2, field['sample'])
            
            # Add checkbox for translation selection
            cb = QCheckBox()
            cb.setChecked(True)
            self.field_tree.setItemWidget(item, 3, cb)
            
        self.mapping_group.setEnabled(True)
        self.translate_btn.setEnabled(True)
        self.log_message(f"Detected {len(fields)} fields in XML")
        
    def edit_field_item(self, item, column):
        if column == 0:  # Only allow editing field name
            item.setFlags(item.flags() | Qt.ItemIsEditable)
        else:
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            
    def get_selected_fields(self):
        selected = []
        for i in range(self.field_tree.topLevelItemCount()):
            item = self.field_tree.topLevelItem(i)
            cb = self.field_tree.itemWidget(item, 3)
            if cb.isChecked():
                field_name = item.text(0)
                path = item.text(1)
                sample = item.text(2)
                selected.append({
                    'name': field_name,
                    'path': path,
                    'sample': sample
                })
        return selected
        
    def log_message(self, message):
        self.log.append(message)
        
    def start_translation(self):
        input_file = self.input_path.text()
        output_file = self.output_path.text()
        field_mapping = self.get_selected_fields()
        source_lang = self.source_lang.currentText()
        target_lang = self.target_lang.currentText()
        
        if not field_mapping:
            QMessageBox.warning(self, "Warning", "Please select at least one field to translate")
            return
            
        if not input_file or not output_file:
            QMessageBox.warning(self, "Warning", "Please select both input and output files")
            return
            
        if not os.path.exists(input_file):
            QMessageBox.warning(self, "Warning", "Input file does not exist")
            return
            
         # Clear previous samples
        self.translation_samples = []
        self.preview_btn.setEnabled(False)

        # Disable UI
        self.set_ui_enabled(False, running=True)
        self.log_message(f"Starting translation from {source_lang} to {target_lang}")
        
        self.worker = TranslationWorker(input_file, output_file, field_mapping, source_lang, target_lang)
        self.worker.progress.connect(self.update_progress)
        self.worker.field_progress.connect(self.update_field_progress)
        self.worker.finished.connect(self.translation_finished)
        self.worker.sample_ready.connect(self.collect_samples)
        self.worker.paused.connect(self.on_paused)
        self.worker.start()

    def toggle_pause(self):
        if self.worker:
            if self.pause_btn.text() == "Pause":
                self.worker.pause()
            else:
                self.worker.resume()
                self.pause_btn.setText("Pause")
                self.log_message("Translation resumed")
        
    def on_paused(self):
        self.pause_btn.setText("Resume")
        self.log_message("Translation paused")

    def stop_translation(self):
        if self.worker:
            self.worker.stop()
            self.log_message("Stopping translation... Please wait")
            self.stop_btn.setEnabled(False)
            
    def set_ui_enabled(self, enabled, running=False):
        self.input_path.setEnabled(enabled)
        self.output_path.setEnabled(enabled)
        self.input_browse.setEnabled(enabled)
        self.output_browse.setEnabled(enabled)
        self.mapping_group.setEnabled(enabled)
        self.source_lang.setEnabled(enabled)
        self.target_lang.setEnabled(enabled)
        self.translate_btn.setEnabled(enabled)
        self.pause_btn.setEnabled(running)
        self.stop_btn.setEnabled(running)
        
    def update_progress(self, current, total, message):
        self.progress_bar.setMaximum(total)
        self.progress_bar.setValue(current)
        self.progress_label.setText(message)
        
    def update_field_progress(self, field_path):
        self.field_label.setText(f"Current field: {field_path}")
        self.log_message(f"Processing {field_path}")

    def collect_samples(self, samples):
        self.translation_samples = samples
        self.preview_btn.setEnabled(True)
        self.log_message("Collected translation samples from first 5 products")
        
    def show_preview(self):
        if not self.translation_samples:
            QMessageBox.information(self, "Preview", "No translation samples available yet")
            return   
        preview = PreviewDialog(self.translation_samples)
        preview.exec_()   
        
    def translation_finished(self, success, message):
        self.set_ui_enabled(True)
        self.log_message(message)
        self.field_label.setText("Current field: None")
        self.pause_btn.setText("Pause")
        
        if success:
            QMessageBox.information(self, "Success", message)
        else:
            QMessageBox.warning(self, "Warning", message)
            
        self.progress_label.setText("Ready")

def main():
    app = QApplication(sys.argv)
    window = TranslationApp()
    window.show()
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()