a<-commandArgs(FALSE);here<-dirname(normalizePath(sub('^--file=','',a[grepl('^--file=',a)][1])))
root<-normalizePath(file.path(here,'../..'));p<-file.path(root,'templates/build-dendra-daily-integrated.template.yml')
x<-yaml::read_yaml(p);on<-x[['on']];if(is.null(on))on<-x[['TRUE']]
stopifnot(identical(names(on),'workflow_dispatch'),identical(x$permissions$contents,'read'),length(x$jobs)==2L,identical(x$jobs$publish$permissions$contents,'write'),identical(x$jobs$publish$concurrency$group,'brim-live-main-publish'))
stopifnot(is.null(x$concurrency),is.null(x$jobs$prepare$concurrency),identical(x$jobs$publish$concurrency$`cancel-in-progress`,FALSE),identical(x$jobs$publish$concurrency$queue,'max'))
n<-0L
for(job in x$jobs)for(step in job$steps)if(!is.null(step$run)){f<-tempfile(fileext='.sh');writeLines(step$run,f);stopifnot(system2('bash',c('-n',shQuote(f)))==0);unlink(f);n<-n+1L}
t<-paste(readLines(p),collapse='\n')
for(s in c('restore-public','combined_selection.json','candidate_relpath','--source-sha "$GITHUB_SHA"','--target-ref refs/heads/main','--owned-root docs/data/dendra/state','--staged-validator scripts/dendra_publisher.py'))stopifnot(grepl(s,t,fixed=TRUE))
stopifnot(!grepl('schedule:|cron:',t),!file.exists(file.path(root,'.github/workflows/build-dendra-daily-integrated.yml')))
cat('PASS integrated manual YAML,',n,'bash blocks, durable restore/bootstrap, transfer, source SHA, ownership and callbacks; static only\n')
