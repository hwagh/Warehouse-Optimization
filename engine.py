AttributeError: This app has encountered an error. The original error message is redacted to prevent data leaks. Full error details have been recorded in the logs (if you're on Streamlit Cloud, click on 'Manage app' in the lower right of your app).
Traceback:
File "/mount/src/warehouse-optimization/streamlit_app.py", line 542, in <module>
    st.plotly_chart(make_flow_diagram(get_engine()), use_container_width=True)
                    ~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^
File "/mount/src/warehouse-optimization/streamlit_app.py", line 522, in make_flow_diagram
    + "Paper->300: " + str(int(fl.paper_to_300)) + "  |  "
                               ^^^^^^^^^^^^^^^
