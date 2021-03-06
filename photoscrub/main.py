# NOTES:
#
#   - You can run `automator -i /path/to/input/file /path/to/workflow` to runs
#     an Automator workflow that requires a File input; we have a `Display
#     referenced photo` in the root of the repository
#
#   - There are several PersonInfo objects with the name '_UNKNOWN_'; each
#     represents what is considered to be an independent person. Each of these
#     has several FaceInfo objects describing their different faces.
#
#   - Unknown if it is possible to find FaceInfo objects that represent faces
#     found in multiple photos, or if they are each single-photo and are
#     aggregated at the PersonInfo layer.
#
#   - The FaceInfo object has (x, y) coordinates for the face location in the
#     image. The range is [0, 1.0]
#
#   - The FaceInfo object has a quality(?) score q=[-1.0, 1.0]. There are some
#     PersonInfo objects with FaceInfo objects that are exclusively -1. One is
#     Mia. But this FaceInfo doesn't actually show up rendered in the Photos
#     application.
#
#       - TODO: Does this FaceInfo have coordinates?
#
#   - Each photo can have multiple FaceInfo associated with it, each with a
#     different PersonInfo.
#
#   - Tagging a single FaceInfo in a PersonInfo doesn't seem to update the rest
#     of the FaceInfos. At least not immediately. Maybe this happens in the
#     background?
#
#       - TODO: Test this
#
#   TODO:
#
#       - Need a way to mark people so that they don't show up in the tool
#         anymore, e.g. someone who we don't know or care about.
#
#       - Need a way to mark photos so that they don't show up in the tool
#         anymore, e.g. a photo that has ONLY people that we don't care about.

import functools
from itertools import islice
import os.path
from subprocess import check_call
import sys
from typing import List, Optional

import osxphotos
from PyQt6.QtWidgets import (
    QApplication,
    QLabel,
    QWidget,
    QGridLayout,
    QVBoxLayout,
    QPushButton,
    QMainWindow,
)
from PyQt6.QtGui import QImage, QPixmap, QPainter, QPen, QColor, QPalette, QResizeEvent
from PyQt6.QtCore import Qt, QPoint, pyqtSignal, pyqtSlot, QRect, QSize


def pdb_photo_to_image(
    pi: osxphotos.PhotoInfo,
    fi: Optional[osxphotos.personinfo.FaceInfo],
) -> QImage:
    qi = QImage(pi.path)

    if fi is not None:
        qp = QPainter(qi)
        pen = QPen(QColor.fromRgb(255, 0, 255))
        pen.setWidth(20)
        qp.setPen(pen)

        qp.drawEllipse(
            QPoint(fi.center[0], fi.center[1]),
            # XXX: What is the right rx/ry?
            int(fi.size * fi.source_width),
            int(fi.size * fi.source_width),
        )

    return qi


class PersonWindow(QWidget):
    person_info: osxphotos.PersonInfo
    open_photo: pyqtSignal = pyqtSignal(osxphotos.PhotoInfo)

    def __init__(self, pi: osxphotos.PersonInfo):
        super(PersonWindow, self).__init__()

        self.person_info = pi

        layout = QGridLayout(self)
        for idx, fi in enumerate(islice(pi.face_info, 9)):
            row = int(idx / 3)
            col = idx % 3

            cw = QWidget()
            layout.addWidget(cw, row, col)

            cl = QVBoxLayout(cw)
            label = QLabel()
            image = pdb_photo_to_image(fi.photo, fi)
            label.setPixmap(
                QPixmap.fromImage(image).scaled(
                    400, 400, Qt.AspectRatioMode.KeepAspectRatio
                )
            )
            cl.addWidget(label)

            pb = QPushButton("Open photo")
            pb.clicked.connect(functools.partial(self.open_photo.emit, fi.photo))
            cl.addWidget(pb)


class PersonPreviewTile(QWidget):
    """
    A tile showing preview of a given PersonInfo.
    """

    person_info: osxphotos.PersonInfo
    open_person: pyqtSignal = pyqtSignal(osxphotos.PersonInfo)
    image: QImage
    label: QLabel

    def __init__(self, pi: osxphotos.PersonInfo, parent: Optional[QWidget] = None):
        super(PersonPreviewTile, self).__init__(parent)

        self.person_info = pi

        # Find the face to use for this person
        for fi in pi.face_info:
            if fi._pk == pi.keyface:
                break
        else:
            raise Exception("Failed to find key face")

        self.image = pdb_photo_to_image(pi.keyphoto, fi)
        self.label = QLabel()

        layout = QVBoxLayout(self)
        layout.addWidget(self.label)
        self.label.setPixmap(
            QPixmap.fromImage(self.image).scaled(
                QSize(100, 100), Qt.AspectRatioMode.KeepAspectRatio
            )
        )

        pb = QPushButton("Open")
        pb.clicked.connect(functools.partial(self.open_person.emit, self.person_info))
        layout.addWidget(pb)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)

        image_width = int(self.size().width() * 0.8)
        image_height = int(self.size().height() * 0.8)

        self.label.setPixmap(
            QPixmap.fromImage(self.image).scaled(
                QSize(image_width, image_height),
                Qt.AspectRatioMode.KeepAspectRatio,
            )
        )


class PeopleWindow(QMainWindow):
    """
    Main window that shows preview tiles for a collection of people.
    """

    person_infos: List[osxphotos.PersonInfo]
    open_person: pyqtSignal = pyqtSignal(osxphotos.PersonInfo)

    def __init__(self, person_infos: List[osxphotos.PersonInfo]):
        super(PeopleWindow, self).__init__()
        self.setWindowTitle("Photoscrub")

        self.person_infos = person_infos
        cw = QWidget(self)
        self.setCentralWidget(cw)

        layout = QGridLayout(cw)
        for r in range(3):
            for c in range(3):
                pi = self.person_infos[r * 3 + c]
                ppt = PersonPreviewTile(pi, cw)
                ppt.open_person.connect(self.clicked)
                layout.addWidget(ppt, r, c)

    @pyqtSlot(osxphotos.PersonInfo)
    def clicked(self, pi: osxphotos.PersonInfo):
        self.open_person.emit(pi)


def load_person_infos(pdb: osxphotos.PhotosDB) -> List[osxphotos.PersonInfo]:
    person_infos: List[osxphotos.PersonInfo] = []
    for pi in sorted(
        [pi for pi in pdb.person_info if pi.facecount > 0 and pi.name == "_UNKNOWN_"],
        key=lambda pi: pi.facecount,
        reverse=True,
    ):
        # XXX: Why?
        if not pi.keyface:
            continue

        # XXX: Why?
        if not pi.keyphoto:
            continue

        person_infos.append(pi)
        if len(person_infos) >= 9:
            break

    return person_infos


def main():
    app = QApplication(sys.argv)

    pdb = osxphotos.PhotosDB()
    person_infos = load_person_infos(pdb)

    person_window: PersonWindow = None

    @pyqtSlot(osxphotos.PhotoInfo)
    def open_photo_clicked(pi: osxphotos.PhotoInfo) -> None:
        check_call(
            args=[
                "automator",
                "-i",
                pi.path,
                os.path.join(
                    os.path.dirname(__file__), "..", "Display referenced photo.workflow"
                ),
            ]
        )

    @pyqtSlot(osxphotos.PersonInfo)
    def open_person_clicked(pi: osxphotos.PersonInfo) -> None:
        nonlocal person_window

        person_window = PersonWindow(pi)
        person_window.open_photo.connect(open_photo_clicked)

        person_window.show()

    people_window = PeopleWindow(person_infos)

    # Resize and center the window
    ag = people_window.screen().availableGeometry()
    width = min(int(ag.width() * 0.8), 2000)
    height = min(int(ag.height() * 0.8), 1000)
    people_window.setGeometry(
        QRect(
            QPoint(
                int((ag.width() - ag.x() - width) / 2 + ag.x()),
                int((ag.height() - ag.y() - height) / 2 + ag.y()),
            ),
            QSize(width, height),
        )
    )

    people_window.open_person.connect(open_person_clicked)
    people_window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
