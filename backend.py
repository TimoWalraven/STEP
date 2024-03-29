import sys
import time

from PySide6.QtWidgets import QApplication, QMainWindow, QFileDialog, QTableWidgetItem
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QShortcut
from threading import Thread
import serial
from serial.tools import list_ports
import datetime
from time import sleep
import numpy as np
from pyentrp import entropy as ent
# Custom imports
import frontend
from code_descriptors_postural_control.descriptors import compute_all_features
from code_descriptors_postural_control.stabilogram.stato import Stabilogram


class STEPviewer:

    def __init__(self, dummy=False):
        # Setup frontend
        self.app = QApplication.instance()  # checks if QApplication already exists
        if not self.app:  # create QApplication if it doesn't exist
            self.app = QApplication(sys.argv)
        self.ui = frontend.Ui_MainWindow()
        self.win = QMainWindow()
        self.ui.setupUi(self.win)
        self.win.showMaximized()

        # global variables
        try:
            import configparser
            config = configparser.ConfigParser()
            self.config = config.read('STEPconfig.ini')
            self.config = {
                "practitioner": config['GENERAL']['practitioner'],
                "url": config['RESEARCHDRIVE']['url'],
                "username": config['RESEARCHDRIVE']['username'],
                "password": config['RESEARCHDRIVE']['password']
            }
        except Exception as e:
            print(f"Error while reading config file: {e}")
            self.config = {
                "practitioner": "unknown",
                "url": None,
                "username": None,
                "password": None
            }

        self.ui.modes.currentChanged.connect(self.switchmode)
        self.mode = self.ui.modes.currentIndex()

        self.livex = []
        self.livey = []
        self.analysisdata = np.array([])

        self.idx = 0

        self.display = False

        # Live mode variables
        self.status = 'disconnected'
        #self.ui.statustext.setText(f"initializing...")

        self.com_list = []
        self.com_port_selected = None

        self.recording = []
        self.recordinginfo = {
            "date": "",
            "time": "",
            "duration": "",
            "stance": "",
            "eyes": "",
            "identifier": "",
            "age": "",
            "height": "",
            "weight": "",
            "condition": "",
            "medication": "",
            "fallhistory": "",
            "notes": ""
        }
        self.recordstate = False

        if dummy:
            dummypath = 'testrecordings/STEP_dummyrecording.xlsx'
            try:
                self.openrecording(filename=dummypath)
            except Exception as e:
                print(f"Error while opening file: {e}")
                return
            self.livex = [i[1] for i in self.recording]
            self.livey = [i[2] for i in self.recording]

        # Analysis mode variables
        self.analysisidx = 0  # index of current measurement
        self.playstate = False  # state of the play button
        self.variables = None  # dictionary of variables

        # Live mode setup
        # Toolbar: from left to right
        self.ui.startrecording.clicked.connect(self.recorder)

        self.ui.analyserecording.clicked.connect(self.analyserecording)
        if not dummy:
            self.ui.analyserecording.setDisabled(True)

        # Patient info
        self.ui.identifierreload.clicked.connect(self.randompatient)

        # Plots
        self.ui.liveapwidget.setmode('AP', live=True)
        self.ui.livemlwidget.setmode('ML', live=True)

        # Analysis mode setup
        # shortcuts
        # TODO: investigate why this prints space as well
        self.shortcut = QShortcut(Qt.Key_Space, self.win)
        self.shortcut.activated.connect(self.playpause)
        # Toolbar: from left to right
        # Open file button
        self.ui.analysisopenfile.clicked.connect(self.openrecording)
        # Play/pause button
        self.ui.analysisplay.clicked.connect(self.playpause)
        self.ui.analysisplay.setDisabled(True)
        # Slider current value
        self.ui.currenttime.setText(f'--')
        # Slider
        self.ui.timeslider.valueChanged.connect(self.slider_changed)
        self.ui.timeslider.sliderPressed.connect(self.slider_pressed)
        self.ui.timeslider.sliderReleased.connect(self.slider_released)
        self.ui.timeslider.setDisabled(True)
        # Slider max value
        self.ui.maxtime.setText(f'--')
        # Restart button
        self.ui.analysisrestart.clicked.connect(self.restart)
        self.ui.analysisrestart.setDisabled(True)
        # Save button
        self.ui.saverecording.clicked.connect(self.saverecording)  # save button
        self.ui.saverecording.setDisabled(True)

        # Patient info
        self.ui.identifierreload_2.clicked.connect(self.randompatient)

        # Plots
        self.ui.analysisapwidget.setmode('AP')
        self.ui.analysismlwidget.setmode('ML')

        # Initialize timer for updating the plot and elapsed time
        self.start_time = datetime.datetime.now()
        # TODO: set interval to automatically correlate with target frequency
        self.interval = 10  # Interval in milliseconds
        self.timer = QTimer()
        self.timer.timeout.connect(self.update)
        self.timer.start(self.interval)  # Start the timer

        # threading
        self.readthread = Thread(target=self.read_from_serial)
        self.readthread.daemon = True  # Daemon threads are terminated when the main program exits.
        self.readthread.start()

        # start application
        self.win.show()
        print("GUI initialized.")
        self.app.exec()

    def update_com(self):
        """
        Method that gets the list of available coms in the system
        """
        # update com list
        ports = list_ports.comports()
        # exclude bluetooth serial ports
        ports = set([port for port in ports if 'Bluetooth' not in port[1]])
        if len(ports) == 0:
            self.com_list = {'No com ports found'}
        else:
            self.com_list = set([com[0] for com in ports])

        # update com port dropdown
        # get current com ports
        current_coms = set([self.ui.comport.itemText(i) for i in range(self.ui.comport.count())])
        if current_coms != self.com_list:
            self.ui.comport.clear()
            self.ui.comport.addItems(self.com_list)

    def read_from_serial(self):
        while True:
            if self.mode == 0:
                self.display = False
                self.update_com()
                if self.com_port_selected != self.ui.comport.currentText() and self.ui.comport.currentText() in self.com_list:
                    self.com_port_selected = self.ui.comport.currentText()
                    print(f'com port selected: {self.com_port_selected}')
                if self.com_port_selected is not None and self.com_port_selected != 'No com ports found':
                    try:
                        print(f"Opening serial port {self.com_port_selected}...")
                        with serial.Serial(self.com_port_selected, 9600, timeout=1) as ser:
                            print(f"Serial port {ser.name} successfully opened.")
                            self.status = 'connected'
                            line = ''
                            while self.com_port_selected == self.ui.comport.currentText() and self.mode == 0:
                                incoming = ser.readline().decode()
                                if incoming and incoming != '':
                                    self.status = 'display'
                                    line += incoming[1:-2]
                                    self.livex.append(float(line.split(', ')[0]))
                                    self.livey.append(float(line.split(', ')[1]))
                                    line = ''
                                    if len(self.livex) > 50:
                                        self.livex.pop(0)
                                        self.livey.pop(0)
                                else:
                                    self.display = 'connected'
                                    time.sleep(0.01)
                                    continue

                    except serial.SerialException as e:
                        print(f"An error occurred: {e} \n Trying to reconnect...")
                        self.status = 'disconnected'
                        self.display = False
                        sleep(1)
                else:
                    sleep(1)
            else:
                sleep(1)

    def update(self):
        # Live mode
        # TODO: add correct time to live plot
        if self.mode == 0:
            self.ui.livestabilogramwidget.line.setData(self.livex, self.livey)
            self.ui.liveapwidget.line.setData(np.linspace(0, 30, len(self.livey)), self.livey)
            self.ui.livemlwidget.line.setData(np.linspace(0, 30, len(self.livex)), self.livex)
            # Status light
            if self.status == 'display' and self.ui.statuslight.styleSheet() != ("background-color: green; "
                                                                                 "border-radius: 10px"):
                self.ui.statuslight.setStyleSheet("background-color: green; border-radius: 10px")
            elif self.status == 'disconnected' and self.ui.statuslight.styleSheet() != ("background-color: red; "
                                                                                        "border-radius: 10px"):
                self.ui.statuslight.setStyleSheet("background-color: red; border-radius: 10px")
            elif self.status == 'connected' and self.ui.statuslight.styleSheet() != ("background-color: orange; "
                                                                                     "border-radius: 10px"):
                self.ui.statuslight.setStyleSheet("background-color: orange; border-radius: 10px")

            # status text
            """if self.status != self.ui.statustext.text():
                self.ui.statustext.setText(self.status)"""
            # record button color
            if self.recordstate and self.ui.startrecording.styleSheet() != "background-color: red":
                self.ui.startrecording.setStyleSheet("background-color: red")
            elif not self.recordstate and self.ui.startrecording.styleSheet() != "background-color: none":
                self.ui.startrecording.setStyleSheet("background-color: none")

        # Analysis mode
        elif self.mode == 1:
            if len(self.analysisdata) > 1:
                self.ui.currenttime.setText(f'{self.analysisdata[self.analysisidx][0]:.2f} s')
                time = self.analysisdata[:self.analysisidx, 0]
                x = self.analysisdata[:self.analysisidx, 1]
                y = self.analysisdata[:self.analysisidx, 2]
                self.ui.analysisstabilogramwidget.line.setData(x, y)
                self.ui.analysisapwidget.line.setData(time, y)
                self.ui.analysismlwidget.line.setData(time, x)
                # update plot
                if self.playstate:
                    self.ui.timeslider.setValue(self.analysisidx)
                if self.playstate and self.analysisidx < len(self.analysisdata) - 1:
                    self.analysisidx += 1
                elif self.playstate and self.analysisidx >= len(self.analysisdata) - 1:
                    self.playstate = False
                    self.ui.analysisplay.setDisabled(True)

        QApplication.processEvents()

    def saverecording(self):

        def sendtoresearchdrive(data, metadata, mode='excel'):

            assert mode in ('excel', 'json'), "Mode must be 'excel' or 'json'"

            print(f"Checking credentials...")
            if None in (self.config['url'], self.config['username'], self.config['password']):
                print("No url, username or password found: please enter your credentials in the config file.")
            # send options request to check if url and credentials are correct
            url = self.config['url']
            username = self.config['username']
            password = self.config['password']
            try:
                import requests
                response = requests.request("OPTIONS", url, auth=(username, password))
                if response.status_code != 200:
                    print(f"Error while checking credentials: {response.status_code}")
                    print(response.text)
                    return
                elif response.status_code == 200:
                    print(f"Credentials verified.")
            except Exception as e:
                print(f"Error while checking credentials: {e}")
                return

            print(f"Generating filename...")
            metadata_json = dict(metadata.iloc[0])
            import json
            metadata_json = json.dumps(metadata_json)
            import os, hashlib
            filename = hashlib.sha256(metadata_json.encode()).hexdigest()

            # convert calculated variables to dataframe
            variables_df = pd.DataFrame(self.variables, index=[0])

            if mode == 'excel':
                print(f"Converting data to excel...")
                filepath = os.path.join(os.getcwd(), f'{filename}.xlsx')
                # Create a Pandas Excel writer using XlsxWriter as the engine.
                writer = pd.ExcelWriter(filepath, engine="xlsxwriter")
                # Convert the dataframe to an XlsxWriter Excel object.
                data.to_excel(writer, sheet_name="Data", index=False)
                metadata.to_excel(writer, sheet_name="Metadata", index=False)
                variables_df.to_excel(writer, sheet_name="Variables", index=False)
                # Close the Pandas Excel writer and output the Excel file.
                writer.close()

            elif mode == 'json':
                print(f"Converting data to json...")
                filepath = os.path.join(os.getcwd(), f'{filename}.json')
                data_json = data.to_json(orient='records')
                variables_json = variables_df.to_json(orient='records')
                file = f'{{"metadata": {metadata_json}, "data": {data_json}, "variables": {variables_json}}}'

                # save temporary file locally
                try:
                    print(f"Saving temp file locally...")
                    with open(filepath, 'w') as f:
                        f.write(file)
                        print(f"File saved locally.")
                except Exception as e:
                    print(f"Error while saving temporary file: {e}")
                    return

            # upload file to research drive
            command = f'curl -T {filepath} -u "{username}:{password}" {url}{filename}'
            try:
                print("Uploading file to research drive..")
                import subprocess
                subprocess.run(command, shell=False)
                print("File uploaded successfully.")
            except Exception as e:
                print(f"Error while uploading file: {e}")
                return
            # delete temporary file
            try:
                print("Deleting temporary file...")
                os.remove(filepath)
                print("Temporary file deleted.")
            except Exception as e:
                print(f"Error while deleting temporary file: {e}")
                return

        if len(self.recording) < 1:
            print("No recording found.")
            return
        elif self.recordstate:
            print("Recording in progress, please wait for recording to finish.")
            return

        filename = QFileDialog.getSaveFileName(self.win, 'Save File', '', 'Excel Files (*.xlsx)')
        import pandas as pd
        data_df = pd.DataFrame(self.recording, columns=['time', 'x', 'y'])
        data_df['time'] = data_df['time'].round(2)
        data_df['x'] = data_df['x'].round(4)
        data_df['y'] = data_df['y'].round(4)

        metadata_df = pd.DataFrame(self.recordinginfo, index=[0])
        metadata_df['age'] = metadata_df['age'].astype(str)
        metadata_df['height'] = metadata_df['height'].astype(str)
        metadata_df['weight'] = metadata_df['weight'].astype(str)
        metadata_df['duration'] = metadata_df['duration'].astype(str)
        metadata_df['date'] = metadata_df['date'].astype(str)
        metadata_df['time'] = metadata_df['time'].astype(str)
        metadata_df['identifier'] = metadata_df['identifier'].astype(str)
        metadata_df['stance'] = metadata_df['stance'].astype(str)
        metadata_df['eyes'] = metadata_df['eyes'].astype(str)
        metadata_df['condition'] = metadata_df['condition'].astype(str)
        metadata_df['medication'] = metadata_df['medication'].astype(str)
        metadata_df['fallhistory'] = metadata_df['fallhistory'].astype(str)
        metadata_df['notes'] = metadata_df['notes'].astype(str)
        metadata_df['practitioner'] = self.config['practitioner']

        variables_df = pd.DataFrame(self.variables, index=[0])

        succes = False
        try:
            # TODO: add graph tab to excel file
            print(f"Saving file to {filename[0]}")
            with pd.ExcelWriter(f'{filename[0]}', engine='xlsxwriter', mode='w') as writer:
                data_df.to_excel(writer, sheet_name='Data', index=False)
                metadata_df.to_excel(writer, sheet_name='Metadata', index=False)
                variables_df.to_excel(writer, sheet_name='Variables', index=False)
                print("File successfully saved locally.")
                succes = True
        except Exception as e:
            print(f"Error while saving file: {e}")
            return

        if succes:
            try:
                if self.ui.contribute.isChecked():
                    sendthread = Thread(target=sendtoresearchdrive, args=(data_df, metadata_df))
                    sendthread.daemon = True
                    sendthread.start()
            except Exception as e:
                print(f"Error while uploading file: {e}")
                return

    def openrecording(self, filename=None):
        if not filename:
            options = QFileDialog.Options()
            options |= QFileDialog.ReadOnly
            filename, _ = QFileDialog.getOpenFileName(self.win, 'Open recording', '', 'Report or raw data (*.xlsx *.json)',
                                                  options=options)
        if filename == '':
            print("No file selected.")
            return

        extention = filename.split('.')[-1]
        if extention == 'xlsx':
            try:
                import pandas as pd
                data = pd.read_excel(filename, sheet_name='Data')
                metadata = pd.read_excel(filename, sheet_name='Metadata')
                self.recording = data.to_numpy()
                self.recordinginfo = {k: str(v[0]) for k, v in metadata.to_dict().items()}
                self.readpatientinfo()
            except Exception as e:
                print(f"Error while opening file: {e}")
                return

        elif extention == 'json':
            try:
                import pandas as pd
                data = pd.read_json(filename, orient='records', typ='series')
                metadata = data['metadata']
                self.recordinginfo = self.recordinginfo | metadata
                data = data['data']
                # generate numpy array from json
                data = np.array([list(i.values()) for i in data])
                self.recording = data
                self.readpatientinfo()
            except Exception as e:
                print(f"Error while opening file: {e}")
                return
        else:
            print("Unknown file extention.")
            return

        try:
            self.analyserecording()
            self.analysisidx = 0
            self.update()
            self.ui.analysisplay.setDisabled(False)
            self.ui.saverecording.setDisabled(False)
            self.ui.analysisrestart.setDisabled(False)
            self.ui.timeslider.setDisabled(False)
            self.ui.currenttime.setText(f'{self.analysisdata[self.analysisidx][0]:.2f} s')
            self.ui.timeslider.setMaximum(len(self.analysisdata) - 1)
            self.ui.maxtime.setText(f'{self.analysisdata[-1][0]:.2f} s')

            # set range of plot by time
            self.ui.analysisapwidget.graph.setRange(xRange=[0, self.analysisdata[-1][0]], yRange=[-115, 115],
                                                    update=True)
            self.ui.analysismlwidget.graph.setRange(xRange=[0, self.analysisdata[-1][0]], yRange=[-220, 220],
                                                    update=True)

            print("file read successfully")

        except Exception as e:
            print(f"Error opening file, maybe wrong format? error: {e}")

    def recorder(self):
        def record(seconds):
            start_time = datetime.datetime.now()
            times = []
            xs = []
            ys = []

            # record for 10 seconds
            print(f"Recording for {seconds} seconds...")
            self.ui.startrecording.setDisabled(True)
            try:
                self.recordstate = True
                self.status = 'recording...'
                while (datetime.datetime.now() - start_time).total_seconds() <= seconds:
                    times.append((datetime.datetime.now() - start_time).total_seconds())
                    xs.append(self.livex[-1])
                    ys.append(self.livey[-1])
                    sleep(0.01)
            except Exception as e:
                print(f"Error while recording: {e}")
                self.recordstate = False
                self.status = f"Error while recording: {e}"
                self.ui.startrecording.setStyleSheet("background-color: none")
                self.ui.startrecording.setDisabled(False)
                return

            self.recordstate = False
            self.status = "Done recording"
            self.ui.startrecording.setStyleSheet("background-color: none")
            print("Done recording")
            self.recording = np.column_stack((times, xs, ys))
            self.recordinginfo = {"date": start_time.strftime("%d/%m/%Y"),
                                  "time": start_time.strftime("%H:%M:%S"),
                                  "duration": self.ui.recordlength.value(),
                                  "stance": self.ui.stanceselect.currentText(),
                                  "eyes": self.ui.eyeselect.currentText(),
                                  "identifier": self.ui.identifierselect.text(),
                                  "age": self.ui.ageselect.value(),
                                  "height": self.ui.heightselect.value(),
                                  "weight": self.ui.weightselect.value(),
                                  "condition": self.ui.conditionselect.currentText(),
                                  "medication": self.ui.medicationselect.currentText(),
                                  "fallhistory": self.ui.fallhistoryselect.currentText(),
                                  "notes": self.ui.notesedit.toPlainText()}

            self.ui.startrecording.setDisabled(False)
            self.ui.analyserecording.setDisabled(False)

        if self.recordstate:
            print("Already recording.")
            return
        elif self.status == 'disconnected':
            print("No balance board connected.")
            return
        elif self.status == 'connected':
            print("Connected to COM, but not receiving data.")
            return
        elif self.status == 'display':
            recordthread = Thread(target=record, args=(self.ui.recordlength.value(),))
            recordthread.daemon = True
            recordthread.start()

        else:
            print("Unknown error occurred.")

    def playpause(self):
        if self.mode == 1 and len(self.analysisdata) > 1:
            if not self.playstate:
                self.start_time = datetime.datetime.now() - datetime.timedelta(
                    seconds=self.analysisdata[self.analysisidx][0])
            self.playstate = not self.playstate

    def restart(self):
        self.analysisidx = 0
        self.playstate = False
        self.playpause()
        self.ui.analysisplay.setDisabled(False)
        self.update()

    def slider_changed(self):
        self.start_time = datetime.datetime.now() - datetime.timedelta(
            seconds=self.analysisdata[self.ui.timeslider.value() - 1][0])
        self.ui.currenttime.setText(f'{self.analysisdata[self.ui.timeslider.value()][0]:.2f} s')
        self.ui.analysisstabilogramwidget.line.setData(self.analysisdata[:self.ui.timeslider.value(), 1],
                                                       self.analysisdata[:self.ui.timeslider.value(), 2])
        self.ui.analysisapwidget.line.setData(self.analysisdata[:self.ui.timeslider.value(), 0],
                                              self.analysisdata[:self.ui.timeslider.value(), 2])
        self.ui.analysismlwidget.line.setData(self.analysisdata[:self.ui.timeslider.value(), 0],
                                              self.analysisdata[:self.ui.timeslider.value(), 1])
        # self.update()

    def slider_pressed(self):
        self.timer.stop()
        self.playstate = False

    def slider_released(self):
        self.analysisidx = self.ui.timeslider.value()
        self.timer.start(self.interval)
        self.update()
        if self.analysisidx != len(self.analysisdata):
            self.ui.analysisplay.setDisabled(False)

    def switchmode(self):
        if self.mode == 0:  # if mode is live (0), switch to analysis (1)
            self.analysisidx = 0
            self.mode = 1

        elif self.mode == 1:  # if mode is analysis (1), switch to live (0)
            self.mode = 0
        else:
            print("Unknown mode.")
        self.update()

    def analyserecording(self):
        def fill_table():
            pass

        target_frequency = 100
        time = self.recording[:, 0]
        x = self.recording[:, 1]
        y = self.recording[:, 2]
        # normalize time, x and y
        time -= time[0]
        x -= np.mean(x)
        y -= np.mean(y)
        data = np.array([time, x, y]).T

        valid_index = (np.sum(np.isnan(data), axis=1) == 0)
        if np.sum(valid_index) != len(data):
            raise ValueError("Clean NaN values first")

        stato = Stabilogram()
        stato.from_array(array=data, resample_frequency=target_frequency)
        # add time to stato
        stato.time = np.linspace(0, len(stato.signal) / target_frequency, len(stato.signal))

        newdata = np.column_stack((stato.time, stato.signal))
        # round to 2 decimals
        newdata = np.round(newdata, 2)
        self.analysisdata = newdata

        # reset analysisidx
        self.analysisidx = 0
        self.ui.timeslider.setValue(0)

        # enable buttons and change mode
        self.ui.analysisplay.setDisabled(False)
        self.ui.saverecording.setDisabled(False)
        self.ui.analysisrestart.setDisabled(False)
        self.ui.timeslider.setDisabled(False)
        self.ui.currenttime.setText(f'{self.analysisdata[self.analysisidx][0]:.2f} s')
        self.ui.timeslider.setMaximum(len(self.analysisdata) - 1)
        self.ui.maxtime.setText(f'{self.analysisdata[-1][0]:.2f} s')
        self.ui.analysisapwidget.graph.setRange(xRange=[0, self.analysisdata[-1][0]], yRange=[-115, 115], update=True)
        self.ui.analysismlwidget.graph.setRange(xRange=[0, self.analysisdata[-1][0]], yRange=[-220, 220], update=True)
        self.ui.modes.setCurrentIndex(1)

        # recording info
        self.recordinginfo['duration'] = f"{self.analysisdata[-1][0]:.2f} s"
        self.recordinginfo['stance'] = self.ui.stanceselect.currentText()
        self.recordinginfo['eyes'] = self.ui.eyeselect.currentText()
        self.recordinginfo['identifier'] = self.ui.identifierselect.text()
        self.recordinginfo['age'] = self.ui.ageselect.value()
        self.recordinginfo['height'] = self.ui.heightselect.value()
        self.recordinginfo['weight'] = self.ui.weightselect.value()
        self.recordinginfo['condition'] = self.ui.conditionselect.currentText()
        self.recordinginfo['medication'] = self.ui.medicationselect.currentText()
        self.recordinginfo['fallhistory'] = self.ui.fallhistoryselect.currentText()
        self.recordinginfo['notes'] = self.ui.notesedit.toPlainText()

        self.readpatientinfo()

        # Table view
        # TODO: recording needs to be >= 11 seconds for this to work without errors
        # TODO: convert to function
        features = compute_all_features(stato)
        self.variables = features

        #compute entropy
        print("Computing entropy...")
        features["entropy_AP"] = ent.sample_entropy(stato.signal[:,0], 2, 0.2 * np.std(stato.signal))[1]
        features["entropy_ML"] = ent.sample_entropy(stato.signal[:,1], 2, 0.2 * np.std(stato.signal))[1]
        print("Entropy computed.")

        # AP/ML variables
        ap_variables = {
            "mean_distance_": ["Mean distance", "mm", '3,4'],
            "rms_": ["RMS", "mm", '4,5'],
            "range_": ["Range", "mm", '22,2'],
            "mean_velocity_": ["Mean velocity", "mm/s", '8.8'],
            "entropy_": ["Entropy", "no unit", 'unknown'],
        }
        # AP variables
        self.ui.apvariables.setRowCount(len(ap_variables))
        self.ui.apvariables.setColumnCount(4)
        self.ui.apvariables.setHorizontalHeaderLabels(['Feature', 'Value', 'Reference', 'Unit'])
        for i, (key, value) in enumerate(ap_variables.items()):
            self.ui.apvariables.setItem(i, 0, QTableWidgetItem(value[0]))
            self.ui.apvariables.setItem(i, 1, QTableWidgetItem(str(features[f'{key}AP'].round(2))))
            self.ui.apvariables.setItem(i, 2, QTableWidgetItem(value[2]))
            self.ui.apvariables.setItem(i, 3, QTableWidgetItem(value[1]))

        ml_variables = {
            "mean_distance_": ["Mean distance", "mm", '1,3'],
            "rms_": ["RMS", "mm", '1,6'],
            "range_": ["Range", "mm", '8,6'],
            "mean_velocity_": ["Mean velocity", "mm/s", '5,9'],
            "entropy_": ["Entropy", "no unit", 'unknown'],
        }
        # ML variables
        self.ui.mlvariables.setRowCount(len(ml_variables))
        self.ui.mlvariables.setColumnCount(4)
        self.ui.mlvariables.setHorizontalHeaderLabels(['Feature', 'Value', 'Reference', 'Unit'])
        for i, (key, value) in enumerate(ml_variables.items()):
            self.ui.mlvariables.setItem(i, 0, QTableWidgetItem(value[0]))
            self.ui.mlvariables.setItem(i, 1, QTableWidgetItem(str(features[f'{key}ML'].round(2))))
            self.ui.mlvariables.setItem(i, 2, QTableWidgetItem(value[2]))
            self.ui.mlvariables.setItem(i, 3, QTableWidgetItem(value[1]))

        # general variables
        variables = {"mean_distance_Radius": ["Mean distance radius", "mm", '11,8'],
                     }

        self.ui.generalvariables.setRowCount(len(variables))
        self.ui.generalvariables.setColumnCount(4)
        self.ui.generalvariables.setHorizontalHeaderLabels(['Feature', 'Value', 'Reference', 'Unit'])
        for i, (key, value) in enumerate(variables.items()):
            self.ui.generalvariables.setItem(i, 0, QTableWidgetItem(value[0]))
            self.ui.generalvariables.setItem(i, 1, QTableWidgetItem(str(features[key].round(2))))
            self.ui.generalvariables.setItem(i, 2, QTableWidgetItem(value[2]))
            self.ui.generalvariables.setItem(i, 3, QTableWidgetItem(value[1]))



    def readpatientinfo(self):
        try:
            # live mode
            self.ui.stanceselect.setCurrentText(self.recordinginfo['stance'])
            self.ui.eyeselect.setCurrentText(self.recordinginfo['eyes'])
            self.ui.identifierselect.setText(self.recordinginfo['identifier'])
            self.ui.ageselect.setValue(int(self.recordinginfo['age']))
            self.ui.heightselect.setValue(int(self.recordinginfo['height']))
            self.ui.weightselect.setValue(int(self.recordinginfo['weight']))
            self.ui.conditionselect.setCurrentText(self.recordinginfo['condition'])
            self.ui.medicationselect.setCurrentText(self.recordinginfo['medication'])
            self.ui.fallhistoryselect.setCurrentText(self.recordinginfo['fallhistory'])
            self.ui.notesedit.setPlainText(self.recordinginfo['notes'])
            # analysis mode
            self.ui.testinfolabel_2.setText(f"Test Info | {self.recordinginfo['date']} | {self.recordinginfo['time']}")
            self.ui.stanceselect_2.setCurrentText(self.recordinginfo['stance'])
            self.ui.eyeselect_2.setCurrentText(self.recordinginfo['eyes'])
            self.ui.identifierselect_2.setText(self.recordinginfo['identifier'])
            self.ui.ageselect_2.setValue(int(self.recordinginfo['age']))
            self.ui.heightselect_2.setValue(int(self.recordinginfo['height']))
            self.ui.weightselect_2.setValue(int(self.recordinginfo['weight']))
            self.ui.conditionselect_2.setCurrentText(self.recordinginfo['condition'])
            self.ui.medicationselect_2.setCurrentText(self.recordinginfo['medication'])
            self.ui.fallhistoryselect_2.setCurrentText(self.recordinginfo['fallhistory'])
            self.ui.notesedit_2.setPlainText(self.recordinginfo['notes'])
        except Exception as e:
            print(f"Error while reading patient info: {e}")
            return

    def randompatient(self):
        try:
            import random
            import string
            identifier = ''.join(random.choice(string.ascii_uppercase + string.digits) for _ in range(10))
            print(f"Generated random identifier: {identifier}")
            self.recordinginfo['identifier'] = identifier
            self.ui.identifierselect.setText(identifier)
            self.ui.identifierselect_2.setText(identifier)
        except Exception as e:
            print(f"Error while generating random identifier: {e}")
            return


if __name__ == '__main__':
    plot = STEPviewer(dummy=False)
