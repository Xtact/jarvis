from typing import Optional
from dotenv import load_dotenv
from spinner import Spinner
import actions, response_parser, check_point, gpt
import os, sys, time, re, signal, argparse, logging
import ruamel.yaml as yaml



base_model  = gpt.GPT_3_5_TURBO
#    You must execute each task in a step-by-step manner, and verify the outcome of each step before moving on to the next step.
#    "information_and_data_for_future_tasks":[], 
#    "verification_process":<TEXT>,
# // save outcome and important information to database for future use. also make sure restartable.
#                "data_need_to_save":{"type":"DbUpsert", other fields},  // choose meaningfull name, key name can be a short description of the previous action.


class InputTimeoutError(Exception):
    pass

class Assistant:

    GENERAL_DIRECTIONS_PREFIX = """
You are a task creation and scheduling AI. You schedule tasks one by one, and you can only schedule one task at a time.
Your intelligence enables independent decision-making, problem-solving, and auto-programming, reflecting true AI autonomy. 

- CONSTRAINTS:
    Do not generate code that requires API keys or tokens, unless you already have them. 

- CODING STANDARDS:
    When crafting code, ensure it is well-structured and easy to maintain. 
    Make sure handle error on return value and exception, the error message must always indicate the error on what's next to do. 
    Always comment your code to clarify functionality and decision-making processes.
    Do not generate placeholder code.

Note: 
When designing plans, the system should understand the task requirements, context, success criteria, dependencies, constraints, and potential unexpected outcomes. 
The plan consists of a series of steps, each of which is a task.
Each task should be simplified to consist of a single or two actions. It is crucial to provide specific and clear instructions for each task.
If we have plan already, we never change it, we focus on executing the plan.


## ACTIONS:
    Following actions will be distributed among various specialized agents. 
    Each agent has the ability to execute a specific action. 
    In order to interact with an agent, an action must be generated by the system, then dispatched to the agent. 
    The results of the executed action, including a brief description and the textual output of the action, are relayed back to you.

    The "RunPython" action: 
    The agent writes Python code to a file and executes the file using the command "python {file_name} {cmd_args}".
    {"type": "RunPython", "FILE_NAME": "<TEXT>", "timeout": "<TIMEOUT>", "cmd_args": "[TEXT]", "code": "<TEXT>"}

    // Your last step. Summary of all steps you have done and what's next to do for user.
    {"type": "Shutdown", "summary": "<TEXT>"} 

    // used to conduct online searches and retrieve relevant URLs for the query.
    {"type": "SearchOnline", "query": "<QUERY>"}
    
    // to extract specific information from a URL.
    {"type": "ExtractInfo", "url": "<URL>", "instructions": "<INSTRUCTIONS>"}
       
## Customization of Response Format
    Your response should be a JSON object adhering to the provided structure. 
    Feel free to add more fields to json for effective task execution or future reference.
    Here is an example of a valid response format, you should keep the same format:
    {
        // Must have.  Mark the current task with [working] prefix.
        // If a task done or failed mark it with [done] or [failed] prefix, and mark future tasks with [pending] prefix.
        "plan": [ 
            // mark 🔥 if action type is running, mark ✅ if action type is done, mark 🕐 if the action type is pending. 
            "[working] 1. {TASK_DESCRIPTION}, actions required:( [✅]SearchOnline -> [🔥]ExtractInfo).", 
            ...   
            "[pending] N. {TASK_DESCRIPTION}, actions required:( [🕐]ExtractInfo  -> [🕐]RunPython), Depends on({task ids})",
            ... 
            // Final step: verify if the overall goal has been met and generate a summary with user guide on what's next.
        ],

        "current_task_id": "1", // Must have.

        "action": { // Must have. must not empty
            "action_id": previous_action_id + 1, // Must have. 
            "type": "RunPython" // Must have, One of the above action types.
            // args for RunPython.
            "file_name":  // must have. where to save the code.
            "timeout":30 // in seconds
            "cmd_args": {ARGUMENTs}
            "code": // pattern = r"^import", must have
            "code_dependencies": ["<DEPENDENCY1>", ...] // external dependencies for <CODE>
            "code_review": // review of the code, must have, does the code meet the requirements of the task?
            //end of args for RunPython.
            "expect_outcome_of_action":, // Expected outcome after executing action, must be very specific and detail, used for verification.
            "desc":, //detail desc of the action, must have
        },

        "notebook": { // Must have. 
            "retried_count": 3, // Shutdown after retrying 5 times.
            "thoughts":{  // must have, your thoughts about the task, such as what you have learned, what you have done, what you have got, what you have failed, what you have to do next etc.
                "reasoning":<TEXT>,
                "criticism":<TEXT>,
            },
            
            ***must have***
            "review_of_previous_action_result":{   
                "previous_action_id":,   // must have
                "action_desc":,
                expected_outcome_of_action:,
                "status":, // must have, such as "success", "failed", "unknown"
                "failed_reason":, // if status is "failed"
                "summary":[{TEXT}. {inspiration}], // should includes inspiration for the current action above.
            },    
        } 
    }
    #end of json
"""


    def __init__(self):
        self.notebook = ""
        self.tasks_desc = ""

    def input_with_timeout(self, prompt: str, timeout: int) -> Optional[str]:
        signal.signal(signal.SIGALRM, self.signal_handler)
        signal.alarm(timeout)

        try:
            user_input = input(prompt)
            return user_input
        finally:
            signal.alarm(0)

    @staticmethod
    def signal_handler(signum, frame):
        raise InputTimeoutError("Timeout expired")

    def make_hints(self, action, metadata, action_output):
        hints = "" 
        
        if metadata:
            hints += self.get_plan_hints(metadata)
            if action:
                hints += self.get_action_hints(metadata, action, action_output)
#            if metadata.notebook:
#                self.notebook = self.extract_notebook(metadata)
#                hints += f"\n## Your previous notebook:\n{self.notebook}" if self.notebook else ""

        self.tasks_desc = hints

    @staticmethod
    def extract_notebook(metadata):
        result = "{\n"
        notebook = metadata.notebook
        if notebook:
            for k, v in notebook.items():
                if v is not None and v != "":
                    # skip review_of_previous_action_result field
                    if k == "review_of_previous_action_result":
                        continue
                    result += f"  \"{k}\": {v},\n"
        result += "}\n"
        return result

    @staticmethod
    def get_plan_hints(metadata):
        return "\n\n## The previous plan:\n" + "\n".join([f"  - {task}" for task in metadata.plan]) + "\n" if metadata.plan else ""

    @staticmethod
    def get_action_hints(metadata, action, action_output):
        return "\n".join([
                "## I executed the action you required, bellow are the results :",
                f"- Task ID: {metadata.current_task_id}",
                f"- Action: ***{action.short_string()}***",
                f"- Action Results:\n{action_output}",
                "##end of action results\n"
            ])
    
    def initialize(self, args):
        general_directions = self.GENERAL_DIRECTIONS_PREFIX + "\n\n"
        load_dotenv()
        os.makedirs("workspace", exist_ok=True)
        os.chdir("workspace")
        new_plan: Optional[str] = None
        timeout = args.timeout

        goal = ""
        latest_checkpoint = checkpoint_db.load_checkpoint()
        # If a checkpoint exists, load the metadata from it
        if latest_checkpoint:
            logging.info("\nload checkpoint success\n")

            self.tasks_desc = latest_checkpoint['task_description']
            goal = latest_checkpoint['goal']
        else:
            goal = input("What would you like me to do:\n")

        #goal = gpt.revise_goal(goal, base_model)
        logging.info("As of my understanding, you want me to do:\n%s\n", goal)

        return goal, new_plan, timeout, general_directions

    def process_action(self, action, metadata, args, timeout, assistant_response):
        action_output = ""
        if isinstance(action, actions.ShutdownAction):
            logging.info("Shutting down...")
            return False
        if not args.continuous:
            run_action = self.input_with_timeout("Run the action? [Y/n]", timeout)
            if run_action is not None and (run_action.lower() != "y" and run_action != ""):
                return False   
        if action is not None:
            action_output = action.run()
            logging.info(f"\n\nAction: %s, output: %s\n\n", action.short_string(), action_output)
        else:
            logging.info("\n\nNo action to run, response is not valid json or missing fields\n\n")
            self.tasks_desc = f"failed to parse response, is it valid json or missing fields? please review: {assistant_response}"
        
        self.make_hints(action, metadata, action_output)
            
        return True

    def run(self, args):
        goal, new_plan, timeout, general_directions = self.initialize(args)
        refresh = False

        while True:
            action = None
            metadata = None
            try:
                logging.info("========================")
                with Spinner("Thinking..."):
                    try:
                        if refresh:
                            assistant_resp = gpt.chat(goal,
                                                      "Your goal has changed. Please update your plan to reflect your new objective!\n" + general_directions,
                                                      self.tasks_desc, model=base_model)
                            refresh = False
                        else:
                            assistant_resp = gpt.chat(goal, general_directions, self.tasks_desc, model=base_model)
                    except Exception as err:
                        logging.info("%s", err)
                        continue

                if args.verbose:
                    logging.info("ASSISTANT RESPONSE: %s", assistant_resp)
                action, metadata = response_parser.parse(assistant_resp)
                if not self.process_action(action, metadata, args, timeout, assistant_resp):
                    break
                # saving the checkpoint after every iteration
                checkpoint_db.save_checkpoint(self.tasks_desc, goal, assistant_resp)

            except Exception as err:
                logging.error("Error in main: %s", err)
                self.make_hints(action, metadata, str(err))
                time.sleep(1)

                continue
            
            print(f"\n\ncurrent plan: {metadata.plan}\n")
            new_plan = self.get_new_plan(timeout)

            if new_plan:     #refresh the goal, since we changed the plan
                goal = gpt.revise_goal(
                    "Given the following context:\n\n" +
                    f"Original goal: {goal}\n" +
                    "Original plan: \n" +
                    f"{metadata.plan}\n" +
                    "Proposed changes to the plan: \n" +
                    f"{new_plan}\n\n" +
                    "Please provide a revised goal that corresponds with this proposed change in the plan. Only state the revised goal.",
                    base_model
                )

                logging.info("\n\nThe new goal is: %s\n\n", goal)
                new_plan = None
                refresh = True


    def get_new_plan(self, timeout: int) -> Optional[str]:
        try:
            change_plan = self.input_with_timeout("Change the proposed plan? [N/y]", timeout)
        except InputTimeoutError:
            logging.info("Input timed out. Continuing with the current plan...")
            change_plan = None

        if change_plan is not None and change_plan.lower() == "y":
            return input("\nWould you like to change your plan? \nChanges you want to make:")
        else:
            return None



if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=str, default='config.yaml', help='Path to the configuration file')
    parser.add_argument('--timeout', type=int, default=1, help='Timeout for user input')  
    parser.add_argument('--continuous', action='store_true', help='Continuous mode')  # Add this line
    parser.add_argument('--verbose', action='store_true', help='Verbose mode')

    args = parser.parse_args()

    # Load configuration from YAML file
    with open(args.config, 'r') as f:
        config = yaml.safe_load(f)

   # Database configuration
    db_config = config.get('database', {})
    db_name = db_config.get('name', 'jarvis')
    db_user = db_config.get('user', '2bw9XzdKWiSnJgo.root')
    db_password = db_config.get('password', 'password')
    db_host = db_config.get('host', 'localhost')
    db_port = db_config.get('port', 4000)
    ssl = db_config.get('ssl', None)

    # Create an instance of CheckpointDatabase
    checkpoint_db = check_point.CheckpointDatabase(db_name, db_user, db_password, db_host, db_port, ssl)

    # Logging configuration
    logging.basicConfig(level=logging.INFO, format='%(message)s', stream=sys.stdout)

    logging.info("Welcome to Jarvis, your personal assistant for everyday tasks!\n")
   
    assistant_config = config.get('assistant', {})
    args.timeout = args.timeout or assistant_config.get('timeout', 30)
    args.verbose = args.verbose or assistant_config.get('verbose', False)
    args.continuous = args.continuous or assistant_config.get('continuous', False)

    checkpoint_db.create_table()



    # Instantiate and start assistant
    assistant = Assistant()
    assistant.run(args)

