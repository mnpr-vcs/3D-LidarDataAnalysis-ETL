import os
import math
import dpkt
import datetime
import numpy as np

from utils import read_uint32

class Lidar():
    def __init__(self):
        self.point_cloud = []

    def process_data_frame(self, data, frame_idx):
        raise NotImplementedError("Subclasses should implement this!")


class VLP16(Lidar):
    
    # factor distance centimeter value to meter
    FACTOR_CM2M = 0.01

    # factor distance value to cm, each velodyne distance unit is 2 mm
    FACTOR_MM2CM = 0.2

    def __init__(self, dual_mode=False):
        super(VLP16, self).__init__()
        
        self.dual_mode = dual_mode
        self.timing_offsets = self.calc_timing_offsets()

        self.omega = np.array([-15, 1, -13, 3, -11, 5, -9, 7, -7, 9, -5, 11, -3, 13, -1, 15])
        self.count_lasers = 16

    def calc_timing_offsets(self):
        """[summary]

        Returns:
            [type]: [description]
        """
        
        timing_offsets = [[0.0 for x in range(12)] for y in range(32)]  # Init matrix 12 x 32

        # constants
        full_firing_cycle = 55.296  # μs
        single_firing = 2.304  # μs
        
        # compute timing offsets
        for x in range(12):
            for y in range(32):
                if self.dual_mode:
                    dataBlockIndex = (x - (x % 2)) + int((y / 16))
                else:
                    dataBlockIndex = (x * 2) + int((y / 16))
                dataPointIndex = y % 16
                timing_offsets[y][x] = \
                    (full_firing_cycle * dataBlockIndex) + (single_firing * dataPointIndex)
                    
        return np.array(timing_offsets).T

    def process_data_frame(self, data, frame_idx):
        """[summary]

        Args:
            data ([type]): A velodyne packet consisting of 12 (n) blocks and 24 (m) sequences and 16 firing pre sequence
            frame_idx ([type]): [description]

        Returns:
            X,Y,Z-coordinate, azimuth, intensitiy, timestamp of each firing, sequence ordered, shape of each = [384x1]
        """
       
        data = np.frombuffer(data, dtype=np.uint8).astype(np.uint32)

        # frame length = 1206
        timestamp = read_uint32(data, 1200)
        factory = data[1204:]

        # velodyne has 12 blocks each 100 bytes data
        # data-legth = 1206 bytes
        blocks = data[0:1200].reshape(12, 100)

        distances = []
        intensities = []
        azimuth_per_block = []
        
        # iteratie through each block
        for i, block in enumerate(blocks):
            dists, intens, angles = self.read_firing_data(block)
            distances.append(dists)
            intensities.append(intens)
            azimuth_per_block.append(angles)


        azimuth_per_block = np.array(azimuth_per_block)

        ## Note: all these arrray have the same size, number of firing in one packet
        azimuth = self.calc_precise_azimuth(azimuth_per_block).reshape(12, 32)
        distances = np.array(distances)
        intensities = np.array(intensities)

        # now calculate the cartesian coordinate of each point
        X, Y, Z = self.calc_cart_coord(distances, azimuth)

        # calculating timestamp [microsec] of each firing
        timestamps = timestamp + self.timing_offsets

        X = X.flatten()
        Y = Y.flatten()
        Z = Z.flatten()
        intensities = intensities.flatten()
        azimuth = azimuth.flatten()
        timestamps = timestamps.flatten()
        distances = distances.flatten() * self.FACTOR_MM2CM * self.FACTOR_CM2M

        # remeber the last azimuth for roll over checking
        self.cur_azimuth = azimuth

        return X, Y, Z, intensities, azimuth, timestamps, distances

    def calc_precise_azimuth(self, azimuth):
        """Linear interpolation of azimuth values

        Args:
            azimuth ([type]): [description]

        Returns:
            [type]: precision_azimuth
        """   
        
        org_azi = azimuth.copy()

        precision_azimuth = []
        # iterate through each block
        for n in range(12): # n=0..11
            azimuth = org_azi.copy()
            try:
                # First, adjust for an Azimuth rollover from 359.99° to 0°
                if azimuth[n + 1] < azimuth[n]:
                    azimuth[n + 1] += 360.

                # Determine the azimuth Gap between data blocks
                azimuth_gap = azimuth[n + 1] - azimuth[n]
            except:
                if azimuth[n] < azimuth[n-1]:
                    azimuth[n] += 360
                azimuth_gap = azimuth[n] - azimuth[n-1]

            factor = azimuth_gap / 32.
            k = np.arange(32)
            precise_azimuth = azimuth[n] + factor * k
            precision_azimuth.append(precise_azimuth)

        precision_azimuth = np.array(precision_azimuth)
        return precision_azimuth

    def calc_precise_azimuth_2(self, azimuth):
        org_azi = azimuth.copy()

        precision_azimuth = []
        # iterate through each block
        for n in range(12): # n=0..11
            azimuth = org_azi.copy()
            try:
                # First, adjust for an Azimuth rollover from 359.99° to 0°
                if azimuth[n + 1] < azimuth[n]:
                    azimuth[n + 1] += 360.

                # Determine the azimuth Gap between data blocks
                azimuth_gap = azimuth[n + 1] - azimuth[n]
            except:
                azimuth_gap = azimuth[n] - azimuth[n-1]

            # iterate through each firing
            for k in range(32):
                # Determine if you’re in the first or second firing sequence of the data block
                if k < 16:
                    # Interpolate
                    precise_azimuth = azimuth[n] + (azimuth_gap * 2.304 * k) / 55.296
                else:
                    # interpolate
                    precise_azimuth = azimuth[n] + (azimuth_gap * 2.304 * ((k-16) + 55.296)) / 55.296
                if precise_azimuth > 361.:
                    print("Error")
                print(precise_azimuth)
                precision_azimuth.append(precise_azimuth)
        precision_azimuth = np.array(precision_azimuth)
        return precision_azimuth

    def read_firing_data(self, data):
        block_id = data[0] + data[1]*256
        # 0xeeff is upper block
        assert block_id == 0xeeff

        azimuth = (data[2] + data[3] * 256) / 100

        firings = data[4:].reshape(32, 3)
        distances = firings[:, 0] + firings[:, 1] * 256
        intensities = firings[:, 2]
        return distances, intensities, azimuth

    def calc_cart_coord(self, distances, azimuth):
        # convert distances to meters
        distances = distances * self.FACTOR_MM2CM * self.FACTOR_CM2M

        # convert deg to rad
        longitudes = np.tile(self.omega * np.pi / 180., 2)
        latitudes = azimuth * np.pi / 180.

        hypotenuses = distances * np.cos(longitudes)

        X = hypotenuses * np.sin(latitudes)
        Y = hypotenuses * np.cos(latitudes)
        Z = distances * np.sin(longitudes)
        
        return X, Y, Z



