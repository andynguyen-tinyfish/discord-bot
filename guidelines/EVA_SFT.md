# **EVA Trace Evaluation | Success Failure | Tiny Fish Instructions & Guidelines**

TinyFish makes Enterprise Web Agents. Enterprise Web Agents are bots that can interact with websites and browsers like humans, but at enterprise scale or millions of times. 

Our current production Web Agent inside of the [mino.ai](http://mino.ai) platform is internally called EVA. 

TinyFish recently started using Databrick and MLFlow to capture production usage of EVA. This means that we can now see exactly what EVA did and why it did what it did when a real TinyFish customer prompted it. 

We will be using this information to improve the EVA model. 

Before we do this, we must first identify successful and failed EVA sessions, classify the failure, and correct them. This is EXACTLY what we have been doing with click failure analysis and human click capture, as well as the Mind2Web benchmarks. We are doing the same thing with a new system and new tools. 

As always, please feel free to reach out with any questions on UpWork. You can also email me at [kat@tinyfish.ai](mailto:kat@tinyfish.ai) or on WhatsApp at \+17342552013  
---

# *Using the Databricks App*

Navigate to the EVA Annotation App: [https://eva-annotation-app-2130205692713078.aws.databricksapps.com/High\_Confidence\_Traces\_Annotation](https://eva-annotation-app-2130205692713078.aws.databricksapps.com/High_Confidence_Traces_Annotation)

You will need to sign in with your email address. You may be asked to [authorize permissions](?tab=t.dev6dl20ocno#bookmark=id.yvs5luv0ljqr) for the app to access databricks as you since your annotator identity is attached to the app when you are signed in. Click Authorize. 

Assignments are randomly assigned as you work when you request a new task. There is no need to worry about requesting more tasks or selecting tasks.   
---

# *Annotation*

1. Once you are signed in, you will need to click “Start Annotating”  
   1. The icon in the upper right corner of the window should be cycling through different activities. If it’s moving, things are working correctly.   
2. You will be presented with step 0 first  
   1. Step 0 will show you the “User Request” and the “Agent Final Response”  
   2. Read both.   
   3. Assess if the Agent was able to successfully complete the task.   
      1. If yes, mark Task Successful  
      2. If no, mark Task Failed  
         1. Tasks that are successful may appear as failed if the actual response is something like “no appointments available”, “no result” or “unavailable”  
         2. You can click through the agent steps using the ← Prev and Next → buttons beneath the app title.   
         3. At the end, you are given an opportunity to review your annotations before submitting.   
3. After selecting an answer, the app will auto advance you to step 1 of the agent’s process.   
   1. All steps in the process will show  
      1. The User Goal  
      2. Before and After Screenshots  
         1. Hovering over the screenshots will cause the “expand” icons to appear  
      3. Original Action  
      4. Tool Response   
4. When evaluating steps, you are evaluating the ACTION taken and NOT the result or outcome.   
   1. If the action is navigate, we need to know if it navigated to the correct URL.   
      1. \*\*SPECIAL CASE\*\* If the URL is no good or the website is down: this is unsuccessful.   
   2. If the action is click, we only need to know if it did click the element.   
   3. If the action is page description, we only need to know if it was able to generate a description of the page.   
5. Evaluate the step and mark as   
   1. Approve   
      1. Use approve if the agent’s chosen action is the correct next action to take in the current situation.   
      2. Use approve if the agent did complete the action.   
   2. Reject  
      1. Use reject if the agent’s chosen action is not the correct next action to take in the current situation.   
      2. Use reject if the agent did not complete the action.   
         1. Remember, you are not evaluating the outcome or result. Just the actual act of taking the action.   
      3. Once you reject an action as the wrong next step, you can Review and Submit all and it will automatically skip the rest of the steps for that trace.   
6. When you have finished moving through all of the steps in the session, click the “Review & Submit All” button  
7. The Review page will display a table with the steps, the status for each, and if the step was annotated.   
   1. You may go back and review any steps and update responses from this page.   
   2. You can directly navigate back using the buttons beneath the table.   
8. The Review Page also presents TWO (2) final annotations  
   1. Failure Type – ONLY used when there is a failure or a rejected step.   
   2. Improvement Type – ONLY used when there is a failure or a rejected step.   
      1. If the session is successful AND all steps are successful, you may skip these annotation and click “Submit & Next Trace)  
      2. Even if the session was *technically* successful, but you think it could be improved, you can still still add these annotations to submit them to the improvement queue.   
         1. Example: Search reddit for posts related to X. Search was successful, but the posts were not all related to X. We can improve this and an LLM failure type for Response Improvement should be submitted.   
9. For Failure Type there are three options:  
   1. User Input  
      1. Select User input if the reason for the failure is the User’s input.   
         1. Example: The user submitted the wrong URL  
         2. Example: The user gave incomplete information  
         3. Example: The user did not provide a desired action  
   2. System  
      1. Select System if the reason for the failure is related to the following System issues  
         1. Antibot  
         2. Timeout  
         3. Server errors  
         4. Forbidden  
         5. 4xx (400, 401, 403, 404, 405, 408\) errors  
         6. 5xx (500, 501, 502, 503, 504, 507\) errors  
         7. Infrastructure errors  
   3. LLM  
      1. Select LLM if the reason for the failure is related to anything else especially a “choice” made by the system.   
10. For Improvement Type there are three options:   
    1. **ONLY for LLM Failure reasons.**   
    2. Response Improvement  
       1. Select Response Improvement if the agent would have been successful if its final response were better.   
          1. Example: The agent only provided 1 response instead of 5\.   
          2. Example: The agent ignored a filter or limitation  
          3. Example: The agent searched for “trending” instead of using the “trending” tab.   
          4. Example: The agent returned the first option instead of the correct option.   
    3. Step Reduction   
       1. Select Step Reduction if the agent took an incorrect, inefficient, or questionable path that did not directly contribute a successful result.   
          1. Example: Did not pre-filter  
          2. Example:   
    4. Step & Response Improvement  
       1. Select Step & Response Improvement if the agent’s response could be improved and it could have taken a more efficient path to get the answer. 

**IF YOU DO NOT KNOW: ASK. We’ll figure it out together.** 

Loom video walkthrough: [https://www.loom.com/share/9cbd5a8ab4a74f5cbe35c11a0afbabe6](https://www.loom.com/share/9cbd5a8ab4a74f5cbe35c11a0afbabe6)

*When and how to double-check[Loom](https://www.loom.com/share/9cbd5a8ab4a74f5cbe35c11a0afbabe6)*

When: you see something is off and you’re not sure whether the agent chose the correct tool or completed the action.

How:  
Look at the RESULT (After) screenshots to see where the “TinyFish” is swimming; it should be the element it is interacting with or where the action is taking place.

Page state (DOM) section: shows the elements visible on the page with the DOM ID, element name, and element type.

Original action section: shows the chosen tool with its arguments, including the element being interacted with (name, DOM ID), and any additional actions (refer to the tool list below).

Compare all cues together to decide whether the step was successful or failed to interact with the correct element using the correct tool.

---

**EVA Evaluation Q\&A** 

1. What if I just don’t know?  
   1. Use the Release and Skip button.   
   2. Send the trace ID \[which you can copy from the box\] to Kat to check later. 

   

TIP: Check back here often\! I will add to the Q\&A as additional questions are asked. 

# *SPEED TIPS AND TRICKS:* 

1. We are only assessing Success and Failure; NOT accuracy.   
   1. If it looks like everything was successful, click success and move on.   
2. Scan the prompt if it is long. You should be able to quickly glance at the output and know if it was successful without reading the full thing.   
3. If the user input is BAD and it leads to failures or the agent doing odd things to fill in for the poor prompt, submit it with a user input failure even if it is successful and move on.   
4. Once you find a spot where the Agent **CLEARLY** could have skipped some steps, submit it as an LLM, step reduction failure.   
5. If you can mark it for step reduction or output improvement, find the step where things start to go wrong, mark it and submit with a note and the correct failure categories.   
6. 

