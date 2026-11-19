import sys
import unittest
from pathlib import Path
import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import figure5_reconstruction as f


class ReconstructionTests(unittest.TestCase):
    def test_ordinate_calibration_uses_labelled_ticks_not_padded_frames(self):
        for name,ticks in f.TICKS.items():
            ticks=np.array(ticks)
            tolerance={"G_Tot":1,"T_a":.05,"V_anem":.001,"P_grid":.004}[name]
            np.testing.assert_allclose(f.ordinate(name,ticks[:,0]),ticks[:,1],rtol=0,atol=tolerance)
        # The solar frame extends above the labelled 1200 tick.
        self.assertGreater(f.ordinate("G_Tot",372.1087),1290)

    def test_quantized_identical_x_points_keep_distinct_ordered_hours(self):
        h=np.arange(1,8761)
        x=f.XFRAME[0]+(f.XFRAME[1]-f.XFRAME[0])*h/8760
        # Actual figure coordinates are quantized at about 0.06875 PDF points.
        quantized=f.XFRAME[0]+np.round((x-f.XFRAME[0])/.06875)*.06875
        recovered,_=f.ordered_hours(quantized)
        np.testing.assert_array_equal(recovered,h)

    def test_missing_vertices_are_not_filled_by_collapsing_duplicate_x(self):
        h=np.delete(np.arange(1,8761),[100,101,4000,8200])
        x=f.XFRAME[0]+(f.XFRAME[1]-f.XFRAME[0])*h/8760
        recovered,_=f.ordered_hours(x)
        np.testing.assert_array_equal(recovered,h)

    def test_impossible_order_rejected(self):
        with self.assertRaises(ValueError):
            f.ordered_hours(np.array([f.XFRAME[1],f.XFRAME[0],f.XFRAME[1]]))
