# gunicorn loads this file automatically from the working directory, so the
# settings apply no matter how the host builds the start command (Railway's
# builder ignores a Procfile).
#
# One process holds the whole dataset in memory (~140 MB); eight threads let
# requests that wait on Supabase overlap instead of queueing behind each other.
workers = 1
threads = 8
timeout = 60
