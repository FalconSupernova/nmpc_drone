import numpy as np
import casadi as cas
from dynamics import unicycle_dynamics
import matplotlib.pyplot as plt
import time


class ref_generator_2d:
    
    
    def __init__(self, start, goal, max_velocity_step, pred_horizn):

        self.start = np.array(start)
        self.goal = np.array(goal)  # Convert goal to a NumPy array
        self.max_velocity_step = max_velocity_step
        self.pred_horizn = pred_horizn
        self.distance_to_goal_from_start = np.linalg.norm(self.goal[:2] - self.start[:2])

    # def generate_waypoints(self, previous_waypoints, current_state):

    #     current_state = np.array(current_state) # Convert current_state to a NumPy array
    #     waypoints = [] 

    #     direction = self.goal[:2] - current_state[:2]
    #     distance_to_goal = np.linalg.norm(direction)

    #     if distance_to_goal <= self.max_velocity_step:
    #         goal_list = self.goal.tolist()
    #         waypoints = [goal_list for _ in range(self.pred_horizn)]
    #         return np.array(waypoints)

    #     step = self.max_velocity_step * direction / distance_to_goal
            
    #     for _ in range(self.pred_horizn):
    #         current_state[:2] += step
    #         current_state[2] = cas.atan2(direction[1], direction[0])
    #         curr_distance = np.linalg.norm(current_state[:2] - self.start[:2])
            
    #         if curr_distance >= self.distance_to_goal_from_start:
    #             waypoints.append(self.goal.tolist())
    #         else:
    #             waypoints.append(current_state.tolist())

    #     if (previous_waypoints[0].tolist() == waypoints[0]):
    #         # Take one more step
    #         waypoints.pop(0)
    #         last_waypoint = waypoints[self.pred_horizn-1]
    #         waypoints.append(last_waypoint)
            
    #     return np.array(waypoints)
    
    
    ## Continuously moving waypoints
    def generate_waypoints(self, previous_waypoints, current_state):

        current_state = previous_waypoints[0] # Convert current_state to a NumPy array
        waypoints = [] 

        direction = self.goal[:2] - current_state[:2]
        distance_to_goal = np.linalg.norm(direction)

        if distance_to_goal <= self.max_velocity_step:
            goal_list = self.goal.tolist()
            waypoints = [goal_list for _ in range(self.pred_horizn)]
            return np.array(waypoints)

        step = self.max_velocity_step * direction / distance_to_goal
            
        for _ in range(self.pred_horizn):
            current_state[:2] += step
            current_state[2] = cas.atan2(direction[1], direction[0])
            curr_distance = np.linalg.norm(current_state[:2] - self.start[:2])
            
            if curr_distance >= self.distance_to_goal_from_start:
                waypoints.append(self.goal.tolist())
            else:
                waypoints.append(current_state.tolist())

        if (previous_waypoints[0].tolist() == waypoints[0]):
            # Take one more step
            waypoints.pop(0)
            last_waypoint = waypoints[self.pred_horizn-1]
            waypoints.append(last_waypoint)
            
        return np.array(waypoints)




class nmpc_node:


    def __init__(self, num_states, num_controls, 
                 pred_horizn, ctrl_horizn, start,
                 max_velocity, max_angular_velocity, 
                 sampling_time, Q_running, R_running, Q_terminal,
                 num_obstacles, obstacle_centers, safe_distance, min_dist_from_center):

        self.num_states = num_states              # Number of states
        self.num_ctrls = num_controls                  # Control inputs
        self.pred_horizn = pred_horizn                # Prediction horizon
        self.ctrl_horizn = ctrl_horizn                # Control horizon
        self.start = start
        # self.goal = goal            # Goal location
        self.max_velocity = max_velocity
        self.max_angular_velocity = max_angular_velocity
        self.dt = sampling_time
        # Obstacle parameters
        self.num_obstacles = num_obstacles
        self.obstacle_centers = obstacle_centers
        self.safe_distance = safe_distance
        self.min_dist_from_center = min_dist_from_center
        # Constants defined
        self.Q_running = Q_running      # Weights for tracking reference state [x, y, θ]
        self.R_running = R_running      # Weights for control effort [v, ω] - Keep this!
        self.Q_terminal = Q_terminal    # Weights for final state deviation from goal


    def cost_objective(self):

        # --- Define the NEW cost function (Trajectory Tracking) ---
        self.horizn_cost = cas.SX(0)

        # Control cost
        # for k in range(self.ctrl_horizn):
        #     # Control Cost (penalize effort)
        #     control = self.pred_controls[:, k]
        #     self.horizn_cost += control.T @ self.R_running @ control

        # State error cost
        for k in range(self.pred_horizn): # Iterate through the prediction horizon for states
            # State Cost (penalize deviation from REFERENCE trajectory X_ref)
            state_error_xy = self.pred_states[0:2, k] - self.ref_waypoints_params[0:2, k]
            theta_error = self.pred_states[2, k] - self.ref_waypoints_params[2, k]
            theta_error_wrapped = cas.atan2(cas.sin(theta_error), cas.cos(theta_error))

            state_cost = self.Q_running[0,0]*state_error_xy[0]**2 + \
                         self.Q_running[1,1]*state_error_xy[1]**2 + \
                         self.Q_running[2,2]*theta_error_wrapped**2 
            self.horizn_cost += state_cost
            
        # theta_error = self.pred_states[2, self.pred_horizn-1] - self.ref_waypoints_params[2, self.pred_horizn-1]
        # #     # Wrap angle error to be within -pi to pi
        # theta_error_wrapped = cas.atan2(cas.sin(theta_error), cas.cos(theta_error))
        # distance_from_terminal_state_error = self.pred_states[:,0] - self.ref_waypoints_params[:,self.pred_horizn-1]

        # terminal_cost = self.Q_terminal[0,0]*distance_from_terminal_state_error[0]**2 + \
        #                          self.Q_terminal[1,1]*distance_from_terminal_state_error[1]**2 
        # self.horizn_cost += 10*terminal_cost
            
        
    def get_constraints(self,):
        large_number = cas.inf # Use CasADi infinity for bounds
        # Constraints on decision variables (Z)
        # Define large bounds for decision variables (Z) 
        self.lbz = np.full((self.num_ctrls * self.ctrl_horizn + self.num_states * self.pred_horizn,), -large_number)  # Lower bounds for Z
        self.ubz = np.full((self.num_ctrls * self.ctrl_horizn + self.num_states * self.pred_horizn,), large_number)   # Upper bounds for Z

        # General input constraints: 0 <= v_k <= v_max, -ω_max <= ω_k <= ω_max 
        for k in range(self.ctrl_horizn):
            # Linear velocity
            self.lbz[k*self.num_ctrls + 0] = 0        # v_k lower bound (non-negative velocity)
            self.ubz[k*self.num_ctrls + 0] = self.max_velocity    # v_k upper bound
            # Angular velocity
            self.lbz[k*self.num_ctrls + 1] = -self.max_angular_velocity    # ω_k lower bound
            self.ubz[k*self.num_ctrls + 1] = self.max_angular_velocity     # ω_k upper bound
        
        # Define other additional nonlinear constraints 
        self.constraints = [] # Constraint vector
        self.lb_constraints = [] # Lower bounds for additional constraints
        self.ub_constraints = [] # Upper bounds for additional constraints

        # 1. Dynamics Constraints (x_{k+1} = f(x_k, u_k)) - Uses symbolic x0 from p
        for k in range(self.pred_horizn):

            x_curr_step = self.pred_states[:, k-1] if k > 0 else self.current_state # State at start of interval k
            u_curr_step = self.pred_controls[:, k]                   # Control during interval k

            x_next_pred = unicycle_dynamics(x_curr_step, u_curr_step, self.dt) # Predicted state at end of interval
            self.constraints.append(self.pred_states[:, k] - x_next_pred) # Constraint: x_{k+1} - f(x_k, u_k) = 0

            self.lb_constraints.extend([0.0] * self.num_states) # Equality constraint: lower bound = 0
            self.ub_constraints.extend([0.0] * self.num_states) # Equality constraint: upper bound = 0


        # # 2. Control Barrier Function (CBF) Constraints - NO CHANGE
        for k in range(self.pred_horizn): # For each time step in the prediction horizon
            pos_k = self.pred_states[0:2, k] # Predicted position [x_{k+1}, y_{k+1}] at the END of step k
            for obs_idx in range(self.num_obstacles): # For each obstacle
                obs_center = self.obstacle_centers[obs_idx, :]
                min_dist_sq = self.min_dist_from_center**2
                dist_sq = cas.sumsqr(pos_k - obs_center) # Squared distance from obstacle center
                h_k_obs = dist_sq - min_dist_sq          # Barrier function value for this obstacle
                self.constraints.append(h_k_obs)
                self.lb_constraints.append(0.0)
                self.ub_constraints.append(large_number)

        self.constraints = cas.vertcat(*self.constraints) # Stack constraints into a single vector
        self.lb_constraints = np.array(self.lb_constraints)
        self.ub_constraints= np.array(self.ub_constraints)        
            
            
    def solver(self):

        # --- Define the nonlinear programming problem (NLP) ---
        nlp = {
            'x': self.Z,      # Decision variables
            'p': self.ref_waypoints_params,      # Parameters (now includes x0 and X_ref)
            'f': self.horizn_cost,   # Objective function (now tracks X_ref)
            'g': self.constraints       # Constraints
        }

        # --- Create the solver ---
        solver_opts = {'ipopt': {'print_level': 3, 'sb': 'yes', 'max_iter': 3000, 'tol': 1e-4}} # Inc max_iter
        solver = cas.nlpsol('solver', 'ipopt', nlp, solver_opts)

        # Solve the optimization problem
        # Initial guess (zeros is usually okay, but reference might be better)
        guess = np.zeros(self.num_ctrls * self.ctrl_horizn + self.num_states * self.pred_horizn)


        print("\n--- Starting Solver ---")
        start_time = time.time()
        
        # Pass the p_value containing x_current and X_ref_traj
        sol = solver(x0=guess, p=self.ref_waypoints_params_value, lbx=self.lbz, ubx=self.ubz, lbg=self.lb_constraints, ubg=self.ub_constraints)
        solve_time = time.time() - start_time
        Z_opt = sol['x'].full().flatten()
        stats = solver.stats()

        if stats['success']:
            print(f"Solver found optimal solution in {solve_time:.2f} seconds.")
        else:
            print(f"Solver finished with status: {stats['return_status']} in {solve_time:.2f} seconds.")

        # --- Extract optimal solution ---
        opt_controls = Z_opt[0:self.num_ctrls * self.ctrl_horizn].reshape(self.ctrl_horizn, self.num_ctrls).T
        opt_states = Z_opt[self.num_ctrls * self.ctrl_horizn:].reshape(self.pred_horizn, self.num_states).T
        # opt_states = np.hstack((x_current.reshape(nx, 1), X_opt)) # Prepend initial state

        return opt_controls, opt_states


    def solve_nmpc(self, ref_waypoints, current_state):
        
        # Parameter vector 'p' now contains initial state AND reference waypoints
        self.ref_waypoints_params = cas.SX.sym('p', self.num_states, self.pred_horizn)  # [number of states; current_position + prediction horizon]
        # Create the parameter vector value including the reference trajectory
        self.ref_waypoints_params_value = ref_waypoints.T
        self.current_state = current_state
        
        # Decision variables declared and controls and predicted states reshaped
        self.Z = cas.SX.sym('Z', self.num_ctrls * self.ctrl_horizn + self.num_states * self.pred_horizn)  # [vec(U); vec(X)]
        self.pred_controls = cas.reshape(self.Z[0:self.num_ctrls * self.ctrl_horizn], self.num_ctrls, self.ctrl_horizn)         # Symbolic Controls u0 to u_{N-1}
        self.pred_states = cas.reshape(self.Z[self.num_ctrls * self.ctrl_horizn:], self.num_states, self.pred_horizn)          # Symbolic States x1 to xN
        
        # Define cost objective
        self.cost_objective()
        print(self.horizn_cost)
        
        # Define constraints
        self.get_constraints()
        print(self.constraints)
        
        # Solve NMPC problem
        self.opt_controls, self.opt_states = self.solver()
        print("optimal control:")
        print(self.opt_controls)
        print("optimal states:")
        print(self.opt_states)
        
        
        # --- Calculate CBF (h) values along the optimal trajectory ---

        h_values = np.zeros((self.num_obstacles, self.pred_horizn))
        for k in range(self.pred_horizn):  # Iterate over each time step of the optimal trajectory X_opt
            pos_k_opt = self.opt_states[0:2, k]  # Optimal position at time step k
            for obs_idx in range(self.num_obstacles):
                obs_center = self.obstacle_centers[obs_idx, :]
                
                # Calculate h = ||pos_opt - obs_center||^2 - min_dist_sq
                min_dist_sq = self.min_dist_from_center**2 # Obstacle_radius + safe_distance
                dist_sq = cas.sumsqr(pos_k_opt - obs_center) # Squared distance from obstacle center
                h_k_obs = dist_sq - min_dist_sq          # Barrier function value for this obstacle
                h_values[obs_idx, k] = h_k_obs

        # Find the minimum h value across all obstacles at each time step
        min_h_values = np.min(h_values, axis=0)
        min_overall_h = np.min(min_h_values) # Smallest h value achieved over the entire trajectory
        print(f"Minimum CBF value (h_min) reached during trajectory: {min_overall_h:.4f}")

        if min_overall_h < -1e-4: # Allow small numerical tolerance
            print("WARNING: Safety constraint (h >= 0) appears to be violated!")
        else:
            print("Safety constraint (h >= 0) appears satisfied.")
        
        return self.opt_controls, self.opt_states, min_h_values
        
        
        
        
        # class obstacles_2d:


#     def create_obstacles_in_map():
    

#         obstacle_centers = np.random.rand(num_obstacles, 2) * 1.0 # Spread in [0, 1] box more broadly


#         # Refilter based on start/goal

#         min_start_goal_dist = 0.15 # Allow slightly closer obstacles

#         valid_centers = []

#         for center in obstacle_centers:

#              if np.linalg.norm(center - x_current[:2]) > min_start_goal_dist and \

#                 np.linalg.norm(center - x_goal[:2]) > min_dist_from_center + 0.05: # Ensure goal isn't inside safety zone

#                  valid_centers.append(center)

#         obstacle_centers = np.array(valid_centers)

#         num_obstacles = obstacle_centers.shape[0]


#         print(f"Using {num_obstacles} obstacles.")


#         # Pre-calculate minimum squared distances from centers

#         min_dist_sq_array = np.full(num_obstacles, min_dist_from_center**2)