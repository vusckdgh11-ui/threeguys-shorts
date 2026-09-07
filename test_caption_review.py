import tempfile
import unittest
from pathlib import Path

import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtWidgets import QApplication, QDialog

import app


class CaptionReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt=QApplication.instance() or QApplication([])

    def test_dialog_edits_each_cut_and_font_settings(self):
        seg=app.Segment('sample.mp4',1,3,100,'원래 대본',visual_description='장면 설명')
        dialog=app.CaptionReviewDialog([seg],'Malgun Gothic',74,'#FFFFFF','#101010',5,True)
        dialog.table.item(0,3).setText('직접 수정한 대본')
        dialog.size_box.setValue(88); dialog.text_color='#FFFF00'; dialog.outline_color='#220000'; dialog.outline_box.setValue(7)
        dialog.accept()
        self.assertEqual(dialog.result(),QDialog.Accepted)
        self.assertEqual(dialog.edited_lines(),['직접 수정한 대본'])
        self.assertEqual(dialog.size_box.value(),88)

    def test_ass_uses_selected_font_colors_and_weight(self):
        seg=app.Segment('sample.mp4',0,2,100,'자막')
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'captions.ass'
            app.build_ass(path,[seg],2,font_name='Arial',text_color='#112233',
                          outline_color='#445566',outline_width=7,bold=False)
            content=path.read_text(encoding='utf-8-sig')
        self.assertIn('Style: Caption,Arial,74,&H00332211&',content)
        self.assertIn('&H00665544&,&H60000000,0,0,0,0,100,100,0,0,1,7,',content)


if __name__=='__main__': unittest.main()
