#--------------------------------------------------------------------------
# Software:     InVesalius - Software de Reconstrucao 3D de Imagens Medicas
# Copyright:    (C) 2001  Centro de Pesquisas Renato Archer
# Homepage:     http://www.softwarepublico.gov.br
# Contact:      invesalius@cti.gov.br
# License:      GNU - GPL 2 (LICENSE.txt/LICENCA.txt)
#--------------------------------------------------------------------------
#    Este programa e software livre; voce pode redistribui-lo e/ou
#    modifica-lo sob os termos da Licenca Publica Geral GNU, conforme
#    publicada pela Free Software Foundation; de acordo com a versao 2
#    da Licenca.
#
#    Este programa eh distribuido na expectativa de ser util, mas SEM
#    QUALQUER GARANTIA; sem mesmo a garantia implicita de
#    COMERCIALIZACAO ou de ADEQUACAO A QUALQUER PROPOSITO EM
#    PARTICULAR. Consulte a Licenca Publica Geral GNU para obter mais
#    detalhes.
#--------------------------------------------------------------------------
import numpy as np

import constants as const

import robot.elfin as elfin
import robot.coordinates as coordinates
import robot.elfin_processing as elfin_process


class RobotControl:
    def __init__(self, rc):
        self.trk_init = None
        self.process_tracker = elfin_process.TrackerProcessing()

        self.robot_coordinates = coordinates.RobotCoordinates(rc)
        self.tracker_coordinates = coordinates.TrackerCoordinates()

        self.trck_init_robot = None

        self.robot_tracker_flag = False
        self.target_flag = False
        self.m_change_robot_to_head = None
        self.coord_inv_old = None

        self.arc_motion_flag = False
        self.arc_motion_step_flag = None
        self.target_linear_out = None
        self.target_linear_in = None
        self.target_arc = None
        self.previous_robot_status = False

    def OnRobotConnection(self, data):
        robot_IP = data["robot_IP"]
        self.ElfinRobot(robot_IP)

    def OnUpdateRobotTransformationMatrix(self, data):
        m_tracker_to_robot = data["m_tracker_to_robot"]
        self.tracker_coordinates.SetTrackerToRobotMatrix(m_tracker_to_robot)
        print("Matrix tracker to robot:", m_tracker_to_robot)

    def OnUpdateRobotTargetMatrix(self, data):
        self.robot_tracker_flag = data["robot_tracker_flag"]
        self.m_change_robot_to_head = np.array(data["m_change_robot_to_head"])

    def OnResetProcessTracker(self, data):
        self.process_tracker.__init__()

    def OnUpdateCoordinates(self, data):
        if len(data) > 1:
            coord = data["coord"]
            markers_flag = data["markers_flag"]
            self.tracker_coordinates.SetCoordinates(np.vstack([coord[0], coord[1], coord[2]]), markers_flag)

    def OnUpdateTrackerFiducialsMatrix(self, data):
        self.matrix_tracker_fiducials = np.array(data["matrix_tracker_fiducials"])
        self.process_tracker.SetMatrixTrackerFiducials(self.matrix_tracker_fiducials)

    def ElfinRobot(self, robot_IP):
        print("Trying to connect Robot via: ", robot_IP)
        self.trck_init_robot = elfin.Elfin_Server(robot_IP, const.ROBOT_ElFIN_PORT)
        self.trck_init_robot.Initialize()
        print('Connect to elfin robot tracking device.')

    def get_coordinates_from_tracker_devices(self):
        coord_robot_raw = self.trck_init_robot.Run()
        coord_robot = np.array(coord_robot_raw)
        coord_robot[3], coord_robot[5] = coord_robot[5], coord_robot[3]
        self.robot_coordinates.SetRobotCoordinates(coord_robot)

        coord_raw, markers_flag = self.tracker_coordinates.GetCoordinates()

        return coord_raw, coord_robot_raw, markers_flag

    def robot_motion_reset(self):
        self.trck_init_robot.StopRobot()
        self.arc_motion_flag = False
        self.arc_motion_step_flag = const.ROBOT_MOTIONS["normal"]

    def robot_move_decision(self, distance_target, new_robot_coordinates, current_robot_coordinates, current_head_filtered):
        """
        There are two types of robot movements.
        We can imagine in two concentric spheres of different sizes. The inside sphere is to compensate for small head movements.
         It was named "normal" moves.
        The outside sphere is for the arc motion. The arc motion is a safety feature for long robot movements.
         Even for a new target or a sudden huge head movement.
        1) normal:
            A linear move from the actual position until the target position.
            This movement just happens when move distance is below a threshold (const.ROBOT_ARC_THRESHOLD_DISTANCE)
        2) arc motion:
            It can be divided into three parts.
                The first one represents the movement from the inner sphere to the outer sphere.
                 The robot moves back using a radial move (it use the center of the head as a reference).
                The second step is the actual arc motion (along the outer sphere).
                 A middle point, between the actual position and the target, is required.
                The last step is to make a linear move until the target (goes to the inner sphere)

        """
        #Check if the target is inside the working space
        if self.process_tracker.estimate_robot_target_length(new_robot_coordinates) < const.ROBOT_WORKING_SPACE:
            #Check the target distance to define the motion mode
            if distance_target < const.ROBOT_ARC_THRESHOLD_DISTANCE and not self.arc_motion_flag:
                self.trck_init_robot.SendCoordinates(new_robot_coordinates, const.ROBOT_MOTIONS["normal"])

            elif distance_target >= const.ROBOT_ARC_THRESHOLD_DISTANCE or self.arc_motion_flag:
                actual_point = current_robot_coordinates
                if not self.arc_motion_flag:
                    head_center_coordinates = self.process_tracker.estimate_head_center(current_head_filtered).tolist()

                    target_linear_out, target_arc = self.process_tracker.compute_arc_motion(current_robot_coordinates, head_center_coordinates,
                                                                                                      new_robot_coordinates)
                    self.arc_motion_flag = True
                    self.arc_motion_step_flag = const.ROBOT_MOTIONS["linear out"]

                if self.arc_motion_flag and self.arc_motion_step_flag == const.ROBOT_MOTIONS["linear out"]:
                    coord = target_linear_out
                    if np.allclose(np.array(actual_point), np.array(target_linear_out), 0, 1):
                        self.arc_motion_step_flag = const.ROBOT_MOTIONS["arc"]
                        coord = target_arc

                elif self.arc_motion_flag and self.arc_motion_step_flag == const.ROBOT_MOTIONS["arc"]:
                    head_center_coordinates = self.process_tracker.estimate_head_center(current_head_filtered).tolist()

                    _, new_target_arc = self.process_tracker.compute_arc_motion(current_robot_coordinates, head_center_coordinates,
                                                                                new_robot_coordinates)
                    if np.allclose(np.array(new_target_arc[3:-1]), np.array(target_arc[3:-1]), 0, 1):
                        None
                    else:
                        if self.process_tracker.correction_distance_calculation_target(new_robot_coordinates, current_robot_coordinates) >= \
                                const.ROBOT_ARC_THRESHOLD_DISTANCE*0.8:
                            target_arc = new_target_arc

                    coord = target_arc

                    if np.allclose(np.array(actual_point), np.array(target_arc[3:-1]), 0, 10):
                        self.arc_motion_flag = False
                        self.arc_motion_step_flag = const.ROBOT_MOTIONS["normal"]
                        coord = new_robot_coordinates

                self.trck_init_robot.SendCoordinates(coord, self.arc_motion_step_flag)
            robot_status = True
        else:
            print("Head is too far from the robot basis")
            robot_status = False

        return robot_status

    def robot_control(self, current_tracker_coordinates_in_robot, current_robot_coordinates, markers_flag):
        coord_head_tracker_in_robot = current_tracker_coordinates_in_robot[1]
        marker_head_flag = markers_flag[1]
        #coord_obj_tracker_in_robot = current_tracker_coordinates_in_robot[2]
        #marker_obj_flag = markers_flag[2]
        robot_status = False

        if self.robot_tracker_flag:
            current_head = coord_head_tracker_in_robot
            if current_head is not None and marker_head_flag:
                current_head_filtered = self.process_tracker.kalman_filter(current_head)
                if self.process_tracker.compute_head_move_threshold(current_head_filtered):
                    new_robot_coordinates = self.process_tracker.compute_head_move_compensation(current_head_filtered,
                                                                                    self.m_change_robot_to_head)
                    robot_status = True
                    if self.coord_inv_old is None:
                       self.coord_inv_old = new_robot_coordinates

                    if np.allclose(np.array(new_robot_coordinates), np.array(current_robot_coordinates), 0, 0.01):
                        #avoid small movements (0.01 mm)
                        pass
                    elif not np.allclose(np.array(new_robot_coordinates), np.array(self.coord_inv_old), 0, 5):
                        #if the head moves (>5mm) before the robot reach the target
                        self.trck_init_robot.StopRobot()
                        self.coord_inv_old = new_robot_coordinates
                    else:
                        distance_target = self.process_tracker.correction_distance_calculation_target(new_robot_coordinates, current_robot_coordinates)
                        robot_status = self.robot_move_decision(distance_target, new_robot_coordinates, current_robot_coordinates, current_head_filtered)
                        self.coord_inv_old = new_robot_coordinates
            else:
                print("Head marker is not visible")
                self.trck_init_robot.StopRobot()

        return robot_status
