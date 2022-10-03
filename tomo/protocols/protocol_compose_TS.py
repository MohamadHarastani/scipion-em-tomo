# **************************************************************************
# *
# * Authors:     Alberto García Mena (alberto.garcia@cnb.csic.es) [1]
# *
# * [1] SciLifeLab, Stockholm University
# *
# * This program is free software; you can redistribute it and/or modify
# * it under the terms of the GNU General Public License as published by
# * the Free Software Foundation; either version 2 of the License, or
# * (at your option) any later version.
# *
# * This program is distributed in the hope that it will be useful,
# * but WITHOUT ANY WARRANTY; without even the implied warranty of
# * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# * GNU General Public License for more details.
# *
# * You should have received a copy of the GNU General Public License
# * along with this program; if not, write to the Free Software
# * Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA
# * 02111-1307  USA
# *
# *  All comments concerning this program package may be sent to the
# *  e-mail address 'scipion@cnb.csic.es'
# *
# **************************************************************************

import time
import os
from glob import glob
from pwem.protocols.protocol_import.base import ProtImport
import pyworkflow as pw
from pyworkflow.protocol import params, STEPS_PARALLEL
import pyworkflow.protocol.constants as cons
from tomo.convert.mdoc import MDoc
import pwem.objects as emobj
import tomo.objects as tomoObj
from pwem.objects.data import Transform
from pyworkflow.object import Integer
from tomo.protocols import ProtTomoBase
from pwem.emlib.image import ImageHandler


class ProtComposeTS(ProtImport, ProtTomoBase):
    """ Compose in streaming a set of tilt series based on a sets of micrographs and mdoc files.
    Three time parameters are abailable for the streaming behaviour:
    Time for next tilt, Time for next micrograph and Time for next Tilt Serie
    """
    _devStatus = pw.BETA
    _label = 'Compose Tilt Serie'

    def __init__(self, **args):
        ProtImport.__init__(self, **args)
        self.newSteps = []
        self.TiltSeries = None

    # -------------------------- DEFINES AND STEPS -----------------------
    def _defineParams(self, form):
        form.addSection(label='Import')

        form.addParam('inputMicrographs', params.PointerParam,
                      pointerClass='SetOfMicrographs',
                      important=True,
                      label="Input micrographs",
                      help='Select the SetOfMicrographs to import')

        form.addParam('filesPath', params.PathParam,
                      label="Files directory ot the tiltSerie files",
                      help="Root directory of the tilt-series. "
                           "Will be search the *.mdoc file for each Tilt Serie")

        form.addSection('Streaming')

        form.addParam('dataStreaming', params.BooleanParam, default=True,
                      label="Process data in streaming?",
                      help="Select this option if you want import data as it "
                           "is generated and process on the fly by next "
                           "protocols. In this case the protocol will "
                           "keep running to check new files and will "
                           "update the output Set, which can "
                           "be used right away by next steps.")

        form.addParam('time4NextTilt', params.IntParam, default=180,
                      condition='dataStreaming',
                      label="Time for next Tilt (secs)",
                      help="Delay (in seconds) until the next tilt is "
                            "registered in the mdoc file. After "
                           "timeout,\n if there is no new tilt, the tilt serie"
                           "is considered as completed\n")
        form.addParam('time4NextMic', params.IntParam, default=12,
                      condition='dataStreaming',
                      label="Time for next micograph processed (secs)",
                      help="Delay (in seconds) until the next micograph is "
                        "processed by the previous protocol")
        form.addParam('time4NextTS', params.IntParam, default=1800,
                      condition='dataStreaming',
                      label="Time for next TiltSerie (secs)",
                      help="Interval of time (in seconds) after which, "
                           "if no new tilt serie is detected, the protocol will "
                           "end. "
                           "The default value is  high (30 min) to "
                           "avoid the protocol finishes during the acq of the "
                           "microscope. You can also stop it from right click "
                           "and press STOP_STREAMING.\n")

    def _initialize(self):
        self.listMdocsRead = []
        self.time4NextTS_current = time.time()
        self.ih = ImageHandler()
        self.waitingMdoc = True

    def _insertAllSteps(self):
        self._insertFunctionStep(self._initialize)
        self.CloseStep_ID = self._insertFunctionStep('closeSet',
                                                     prerequisites=[],
                                                     wait=True)
        self.newSteps.append(self.CloseStep_ID)

    def _stepsCheck(self):
        current_time = time.time()
        delay = int(current_time - self.time4NextTS_current)
        if self.waitingMdoc == True:
            self.debug('Timeout for next TiltSerie (.mdoc file) ' +
                    str(self.time4NextTS.get()) + ' segs ...')
        self.waitingMdoc = False
        list_current = self.findMdoc()
        list_remain = [x for x in list_current if x not in self.listMdocsRead]
        # STREAMING CHECKPOINT
        if delay > int(self.time4NextTS.get()):
            output_step = self._getFirstJoinStep()
            if output_step and output_step.isWaiting():
                output_step.setStatus(cons.STATUS_NEW)

        elif list_remain != []:
            self.waitingMdoc = True
            self.listMdocsRead = list_current
            self.time4NextTS_current = time.time()
            new_step_id = self._insertFunctionStep('readMdoc', list_remain,
                                        prerequisites=[], wait=False)
            self.newSteps.append(new_step_id)
            self.updateSteps()

    def closeSet(self):
        pass

    def _getFirstJoinStep(self):
        for s in self._steps:
            if s.funcName == self._getFirstJoinStepName():
                return s
        return None

    def _getFirstJoinStepName(self):
        # This function will be used for streaming, to check which is
        # the first function that need to wait for all micrographs
        # to have completed, this can be overwritten in subclasses
        # (eg in Xmipp 'sortPSDStep')
        return 'closeSet'


    # -------------------------- MAIN FUNCTIONS -----------------------

    def findMdoc(self):
        """
        :return: return a sorted by date list of all mdoc files in the path
        """
        """ return a sorted by date list of all mdoc files in the path """
        fpath = self.filesPath.get()
        self.MDOC_DATA_SOURCE = glob(os.path.join(fpath, '*.mdoc'))
        self.MDOC_DATA_SOURCE.sort(key=os.path.getmtime)
        return self.MDOC_DATA_SOURCE

    def readMdoc(self, list_remains):
        """
        Main function to launch the match with the set of micrographs and
        launch the create of SetOfTiltSeries and each TiltSerie
        :param list_remains: list of mdoc files in the path
        """
        for file2read in list_remains:
                statusMdoc, mdoc_order_angle_list = self.readingMdocTiltInfo(file2read)
                # STREAMING CHECKPOINT
                while time.time() - self.readDateFile(file2read) < \
                        self.time4NextTilt.get():
                    self.debug('Waiting next tilt... ({} tilts found)'.format(
                        len(mdoc_order_angle_list)))
                    time.sleep(self.time4NextTilt.get() / 2)
                    statusMdoc, mdoc_order_angle_list = \
                        self.readingMdocTiltInfo(file2read)
                if statusMdoc == True:
                    if len(mdoc_order_angle_list) < 3:
                        self.error('Mdoc error. Less than 3 tilts in the serie')
                    elif self.matchTS(mdoc_order_angle_list):
                            self.createTS(self.mdoc_obj)
                            #SUMMARY INFO
                            summaryF = self._getPath("summary.txt")
                            summaryF = open(summaryF, "a")
                            summaryF.write(
                                "Tilt Serie ({} tilts) composed from mdoc file: {}\n".
                                format(len(mdoc_order_angle_list), file2read))
                            summaryF.close()

    def readingMdocTiltInfo(self, file2read):
        mdoc_order_angle_list = []
        self.mdoc_obj = MDoc(file2read)
        validation_error = self.mdoc_obj.read(ignoreFilesValidation=True)
        if validation_error:
            self.debug(validation_error)
            return False, mdoc_order_angle_list
        self.info('mdoc file to read: {}'.format(file2read))
        for tilt_metadata in self.mdoc_obj.getTiltsMetadata():
            mdoc_order_angle_list.append((
                tilt_metadata.getAngleMovieFile(),
                '{:03d}'.format(tilt_metadata.getAcqOrder()),
                tilt_metadata.getTiltAngle()))
        return True, mdoc_order_angle_list


    def readDateFile(self, file):
        return os.path.getmtime(file)

    def matchTS(self, mdoc_order_angle_list):
        """
        Edit the self.listOfMics with the ones in the mdoc file
        :param mdoc_order_angle_list: for each tilt:
                filename, acquisitionOrder, Angle
        """
        len_mics_input_1 = self._loadInputList()
        #STREAMING CHECKPOINT
        while len(mdoc_order_angle_list) > len_mics_input_1:
            self.info('Tilts in the mdoc file: {} Micrographs  abailables: {}'.format(
                len(mdoc_order_angle_list), len(self.listOfMics)))
            self.info('Waiting next micrograph...')
            time.sleep(self.time4NextMic.get())
            len_mics_input_2 = self._loadInputList()
            if len_mics_input_2 == len_mics_input_1:
                self.error('{} micrographs were expected but {} were obtained'.
                format(len(mdoc_order_angle_list), len_mics_input_2))
                return False
            len_mics_input_1 = self._loadInputList()

        self.info('Tilts in the mdoc file: {}\n'
                  'Micrographs abailables: {}'.format(
             len(mdoc_order_angle_list), len(self.listOfMics)))

        #MATCH
        list_mdoc_files = [os.path.basename(fp[0]) for fp in mdoc_order_angle_list]
        list_mics_matched = []
        for x, mic in enumerate(self.listOfMics):
            if mic.getMicName() in list_mdoc_files:
                list_mics_matched.append(mic)
        self.listOfMics = list_mics_matched

        if len(self.listOfMics) != len(mdoc_order_angle_list):
            self.info('Micrographs doesnt match with mdoc read')
            return False
        else:
            self.info('Micrographs matched for the mdoc file: {}'.format(
                len(self.listOfMics)))
            return True

    def _loadInputList(self):
        """ Load the input set of mics and create a list. """
        mic_file = self.inputMicrographs.get().getFileName()
        self.info("Loading input db: %s" % mic_file)
        mic_set = emobj.SetOfMicrographs(filename=mic_file)
        mic_set.loadAllProperties()
        self.listOfMics = [m.clone() for m in mic_set]
        mic_set.close()
        return len(self.listOfMics)

    def createTS(self, mdoc_obj):
        """
        Create the SetOfTiltSeries and each TiltSerie
        :param mdocObj: mdoc object to manage
        """
        if self.TiltSeries == None:
            SOTS = self._createSetOfTiltSeries(suffix='')
            SOTS.setStreamState(SOTS.STREAM_OPEN)
            SOTS.enableAppend()
            self._defineOutputs(TiltSeries=SOTS)
            self._defineSourceRelation(self.inputMicrographs, SOTS)
            self._store(SOTS)
        else:
            SOTS = self.TiltSeries
            SOTS.setStreamState(SOTS.STREAM_OPEN)
            SOTS.enableAppend()
            self._store(SOTS)

        file_order_angle_list = []
        accumulated_dose_list = []
        incoming_dose_list = []
        for tilt_metadata in mdoc_obj.getTiltsMetadata():
            file_order_angle_list.append((
                tilt_metadata.getAngleMovieFile(),  # Filename
                '{:03d}'.format(tilt_metadata.getAcqOrder()),  # Acquisition
                tilt_metadata.getTiltAngle()))
            accumulated_dose_list.append(tilt_metadata.getAccumDose())
            incoming_dose_list.append(tilt_metadata.getIncomingDose())

        file_ordered_angle_list = sorted(file_order_angle_list,
                                      key=lambda angle: float(angle[2]))
        #Tilt Serie object
        ts_obj = tomoObj.TiltSeries()
        len_ac = Integer(len(file_ordered_angle_list))
        ts_obj.setAnglesCount(len_ac)
        ts_obj.setTsId(mdoc_obj.getTsId())
        acq = ts_obj.getAcquisition()
        acq.setVoltage(mdoc_obj.getVoltage())
        acq.setMagnification(mdoc_obj.getMagnification())
        ts_obj.getAcquisition().setTiltAxisAngle(mdoc_obj.getTiltAxisAngle())
        origin = Transform()
        ts_obj.setOrigin(origin)
        SOTS.append(ts_obj)

        self.setingTS(SOTS, ts_obj, file_ordered_angle_list,
                      incoming_dose_list, accumulated_dose_list, origin)

        SOTS.setStreamState(SOTS.STREAM_CLOSED)
        ts_obj.write(properties=False)
        SOTS.update(ts_obj)
        SOTS.updateDim()
        SOTS.write()
        self._store(SOTS)

    def setingTS(self, SOTS, ts_obj, file_ordered_angle_list,
                 incoming_dose_list, accumulated_dose_list, origin):
        '''
        Set all the info in each tilt and set the ts_obj information with all
        the tilts
        :param SOTS: Set Ot Tilt Serie
        :param ts_obj: Tilt Serie Object to add tilts
        :param file_ordered_angle_list: list of files sorted by angle
        :param incoming_dose_list: list of dose
        :param accumulated_dose_list: list of accumulated dose
        :param origin: transform matrix
        :return:
        '''
        ts_fn = self._getOutputTiltSeriesPath(ts_obj)
        ts_fn_dw = self._getOutputTiltSeriesPath(ts_obj, '_DW')
        counter_ti = 0
        for f, to, ta in file_ordered_angle_list:
            try:
                for mic in self.listOfMics:
                    if ts_obj.getSamplingRate() == None:
                        ts_obj.setSamplingRate(mic.getSamplingRate())
                    if SOTS.getSamplingRate() == None:
                        SOTS.setSamplingRate(mic.getSamplingRate())
                    if os.path.basename(f) in mic.getMicName():
                        ti = tomoObj.TiltImage()
                        ti.setLocation(mic.getFileName())
                        ti.setTsId(ts_obj.getObjId())
                        ti.setObjId(counter_ti)
                        ti.setIndex(counter_ti + 1)
                        ti.setAcquisitionOrder(int(to))
                        ti.setTiltAngle(ta)
                        ti.setSamplingRate(mic.getSamplingRate())
                        ti.setAcquisition(ts_obj.getAcquisition().clone())
                        ti.getAcquisition().setDosePerFrame(
                            incoming_dose_list[int(to) - 1])
                        ti.getAcquisition().setAccumDose(
                            accumulated_dose_list[int(to) - 1])
                        ti.setTransform(origin)
                        ti_fn, ti_fn_dw = self._getOutputTiltImagePaths(ti)
                        new_location = (counter_ti, ts_fn)

                        self.ih.convert(mic.getFileName(), new_location)
                        ti.setLocation(new_location)
                        if os.path.exists(ti_fn_dw):
                            self.ih.convert(ti_fn_dw, (counter_ti, ts_fn_dw))
                            pw.utils.cleanPath(ti_fn_dw)
                        ts_obj.append(ti)
                        counter_ti += 1
            except Exception as e:
                self.error(e)

    # -------------------------- AUXILIAR FUNCTIONS -----------------------
    def _getOutputTiltSeriesPath(self, ts, suffix=''):
        return self._getExtraPath('%s%s.mrcs' % (ts.getTsId(), suffix))

    def _getOutputTiltImagePaths(self, tilt_image):
        """ Return expected output path for correct movie and DW one.
        """
        base = self._getExtraPath(self._getTiltImageMRoot(tilt_image))
        return base + '.mrc', base + '_Out.mrc'

    def _getTiltImageMRoot(self, tim):
        return '%s_%02d' % (tim.getTsId(), tim.getObjId())


    def _validate(self):
        errors = [] if len(self.inputMicrographs.get()) > 1 else \
            ["More than one Input micrographs is needed to run."]
        return errors

    def _summary(self):
        summary = []

        summaryF = self._getPath("summary.txt")
        if not os.path.exists(summaryF):
            summary.append("No summary file yet.")
        else:
            summaryF = open(summaryF, "r")
            for line in summaryF.readlines():
                summary.append(line.rstrip())
            summaryF.close()

        return summary