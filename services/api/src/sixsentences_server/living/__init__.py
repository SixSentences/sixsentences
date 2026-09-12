"""Living reviews: keep a completed review honest as the literature moves.

The foundation here is the on-demand re-check — above all the retraction alert
('N of your included works have been retracted since your review'), which needs
no LLM. Continuous, scheduled re-runs are the async worker's job (deferred until
there is a deployment target); this module is what that scheduler will call.
"""
