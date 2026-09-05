import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import unittest
from PySide6.QtTest import QTest
from PySide6.QtCore import Qt, QPoint, QRectF
from PySide6.QtGui import QPixmap, QPainter, QColor
from PySide6.QtWidgets import QApplication
import app


class LogoMouseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.qt=QApplication.instance() or QApplication([])

    def setUp(self):
        self.view=app.PreviewView(); self.view.resize(405,720)
        pixmap=QPixmap(400,200); pixmap.fill(Qt.transparent)
        painter=QPainter(pixmap); painter.fillRect(150,70,100,60,QColor('red')); painter.end()
        self.logo=app.ResizePixmapItem(pixmap,self.view.emit_logo)
        self.view.logo_item=self.logo; self.view.sc.addItem(self.logo)
        self.logo.setScale(0.75); self.logo.setPos(350,500)
        self.view.show(); self.qt.processEvents()

    def tearDown(self): self.view.close(); self.qt.processEvents()

    def test_all_transparent_corners_resize_and_keep_opposite_anchor(self):
        for corner in ('tl','tr','bl','br'):
            self.logo.setScale(0.75); self.logo.setPos(350,500); self.qt.processEvents()
            before=self.logo.mapRectToScene(QRectF(self.logo.pixmap().rect()))
            handle=self.logo.handles[corner]
            start=self.view.mapFromScene(handle.scenePos())
            sx=-1 if corner in ('tl','bl') else 1
            sy=-1 if corner in ('tl','tr') else 1
            end=start+QPoint(sx*30,sy*15)
            QTest.mousePress(self.view.viewport(),Qt.LeftButton,Qt.NoModifier,start)
            QTest.mouseMove(self.view.viewport(),end,20)
            QTest.mouseRelease(self.view.viewport(),Qt.LeftButton,Qt.NoModifier,end)
            self.qt.processEvents()
            after=self.logo.mapRectToScene(QRectF(self.logo.pixmap().rect()))
            self.assertGreater(after.width(),before.width()+20,corner)
            self.assertAlmostEqual(after.width()/after.height(),2)
            self.assertAlmostEqual(after.right() if sx<0 else after.left(),before.right() if sx<0 else before.left())
            self.assertAlmostEqual(after.bottom() if sy<0 else after.top(),before.bottom() if sy<0 else before.top())

    def test_body_drag_moves_without_resizing(self):
        before=self.logo.mapRectToScene(QRectF(self.logo.pixmap().rect()))
        start=self.view.mapFromScene(before.center()); end=start+QPoint(25,20)
        QTest.mousePress(self.view.viewport(),Qt.LeftButton,Qt.NoModifier,start)
        QTest.mouseMove(self.view.viewport(),end,20)
        QTest.mouseRelease(self.view.viewport(),Qt.LeftButton,Qt.NoModifier,end)
        self.qt.processEvents()
        after=self.logo.mapRectToScene(QRectF(self.logo.pixmap().rect()))
        self.assertAlmostEqual(after.width(),before.width())
        self.assertGreater(after.left(),before.left()+20)


if __name__=='__main__': unittest.main()
