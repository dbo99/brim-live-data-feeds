# Serial GETs, one fresh HTTP/1.1 connection per attempt. The native size
# limit also applies to transfers without a known Content-Length (curl 8.4+).
snow_daily_http <- function(transfer = curl::curl_fetch_memory) {
  if (numeric_version(curl::curl_version()$version) < "8.4.0")
    stop("SWE requires libcurl 8.4+ for streaming size limits")
  attempts <- c(NRCS = 0L, CDEC = 0L)
  limits <- c(NRCS = 32L, CDEC = 288L)
  charged <- 0
  held <- NULL
  stop_run <- function(reason) {
    held <<- reason
    stop(reason, call. = FALSE)
  }
  check <- function() {
    if (!is.null(held)) stop(held, call. = FALSE)
  }
  get <- function(url, timeout) {
    check()
    provider <- if (startsWith(url, "https://wcc.sc.egov.usda.gov/awdbRestApi/services/v1/data?")) {
      "NRCS"
    } else if (startsWith(url, "https://cdec.water.ca.gov/dynamicapp/req/JSONDataServlet?")) {
      "CDEC"
    } else stop_run("Unapproved SWE endpoint")
    if (attempts[[provider]] >= limits[[provider]]) {
      error <- simpleError(paste(provider, "attempt cap"))
      class(error) <- c("snow_provider_cap", class(error))
      stop(error)
    }
    allowance <- min(4 * 1024^2, 64 * 1024^2 - charged)
    if (allowance <= 0) stop_run("SWE aggregate body cap")
    handle <- curl::new_handle(followlocation = FALSE, maxredirs = 0L, proxy = "",
                               http_version = 2L, fresh_connect = TRUE, forbid_reuse = TRUE,
                               accept_encoding = "identity", http_content_decoding = FALSE,
                               maxfilesize = allowance, timeout = timeout, connecttimeout = min(30, timeout),
                               useragent = "BRIM-snow-pillow-latest/1.0")
    # Charge before I/O. Release unused bytes only for a complete measured response.
    attempts[[provider]] <<- attempts[[provider]] + 1L
    charged <<- charged + allowance
    message("SWE_HTTP reserve ", provider, " attempt=", attempts[[provider]],
            " body_allowance=", allowance, " cumulative_reserved=", charged)
    measured <- NA_integer_
    status <- NA_integer_
    on.exit(message("SWE_HTTP ", provider, " attempts=", attempts[[provider]],
                    " status=", status, " body_bytes=", measured, " cumulative_charge=", charged))
    resp <- tryCatch(transfer(url, handle = handle), error = identity)
    info <- if (inherits(resp, "error")) curl::handle_data(handle) else resp
    status <- info$status_code
    if (!inherits(resp, "error")) {
      measured <- length(resp$content)
      if (measured > allowance) stop_run("Native SWE response exceeded its bound")
      charged <<- charged - (allowance - measured)
    }
    if (inherits(resp, "curl_error_filesize_exceeded")) stop_run("SWE response or aggregate body cap")
    headers <- rawToChar(info$headers)
    if (status %in% c(401L, 403L, 429L) || (status >= 300L && status < 400L) ||
        grepl("(?im)^retry-after[[:blank:]]*:", headers, perl = TRUE))
      stop_run(paste(provider, "redirect, refusal or provider backoff"))
    if (inherits(resp, "error")) stop(resp)
    resp
  }
  list(get = get, check = check)
}
