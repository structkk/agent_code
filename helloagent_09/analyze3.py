import re,os
files=["CodebaseMaintainer.py","context_builder_agent.py","context_builder_base.py","notetoll_builder.py"]
for f in files:
 c=open(f,encoding="utf-8").read()
 if "_retrieve_relevant_notes" in c: print(f+" has _retrieve_relevant_notes")
 if "_notes_to_packets" in c: print(f+" has _notes_to_packets")
 if "_update_history" in c: print(f+" has _update_history")
print("done")
