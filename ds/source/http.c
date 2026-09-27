#include "http.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <netdb.h>
#include <unistd.h>

// Simple HTTP client for DS
// Note: This is a minimal implementation suitable for DS constraints

#define HTTP_BUFFER_SIZE 4096
#define HTTP_TIMEOUT 10

static int socket_fd = -1;

// Connection debug output (on by default; batch jobs turn it off)
static int http_verbose = 1;
#define HTTP_LOG(...) do { if (http_verbose) iprintf(__VA_ARGS__); } while (0)

void http_set_verbose(int verbose) {
    http_verbose = verbose;
}

int http_init(void) {
    // Sockets are already available through libc on DS
    return 0;
}

// Parse URL into host and path
static int parse_url(const char *url, char *host, int *port, char *path) {
    // Simple URL parser: http://host:port/path or http://host/path
    const char *start = url;
    
    // Skip protocol
    if (strncmp(url, "http://", 7) == 0) {
        start = url + 7;
    } else if (strncmp(url, "https://", 8) == 0) {
        // HTTPS not supported on DS
        return -1;
    }
    
    // Extract host and optional port
    char *colon = strchr(start, ':');
    char *slash = strchr(start, '/');
    
    int host_len;
    if (colon && (!slash || colon < slash)) {
        // Port specified
        host_len = colon - start;
        strncpy(host, start, host_len);
        host[host_len] = '\0';
        *port = atoi(colon + 1);
    } else if (slash) {
        // No port
        host_len = slash - start;
        strncpy(host, start, host_len);
        host[host_len] = '\0';
        *port = 80;
    } else {
        // No slash
        strcpy(host, start);
        *port = 80;
        strcpy(path, "/");
        return 0;
    }
    
    // Extract path
    if (slash) {
        strcpy(path, slash);
    } else {
        strcpy(path, "/");
    }
    
    return 0;
}

HttpResponse http_request_ex(
    const char *url,
    HttpMethod method,
    const char *api_key,
    const char *content_type,
    const uint8_t *body,
    size_t body_size
) {
    HttpResponse response = {0};
    char host[256] = {0};
    char path[512] = {0};
    int port = 80;
    
    HTTP_LOG("\n=== HTTP Debug ===\n");
    HTTP_LOG("URL: %s\n", url);
    
    // Parse URL
    if (parse_url(url, host, &port, path) != 0) {
        HTTP_LOG("URL parse failed!\n");
        response.success = 0;
        return response;
    }
    
    HTTP_LOG("Host: %s\n", host);
    HTTP_LOG("Port: %d\n", port);
    HTTP_LOG("Path: %s\n", path);
    
    // Resolve host
    HTTP_LOG("Resolving DNS...\n");
    struct hostent *he = gethostbyname(host);
    if (!he) {
        HTTP_LOG("DNS lookup failed for %s\n", host);
        response.success = 0;
        return response;
    }
    
    char *ip = inet_ntoa(*(struct in_addr*)he->h_addr_list[0]);
    HTTP_LOG("Resolved to: %s\n", ip);
    
    // Create socket
    HTTP_LOG("Creating socket...\n");
    socket_fd = socket(AF_INET, SOCK_STREAM, 0);
    if (socket_fd < 0) {
        HTTP_LOG("Socket creation failed\n");
        response.success = 0;
        return response;
    }
    HTTP_LOG("Socket created: %d\n", socket_fd);
    
    // Set socket timeout (30 seconds)
    struct timeval tv;
    tv.tv_sec = 30;
    tv.tv_usec = 0;
    setsockopt(socket_fd, SOL_SOCKET, SO_RCVTIMEO, (const char*)&tv, sizeof(tv));
    setsockopt(socket_fd, SOL_SOCKET, SO_SNDTIMEO, (const char*)&tv, sizeof(tv));
    
    // Connect to host
    struct sockaddr_in server_addr;
    memset(&server_addr, 0, sizeof(server_addr));
    server_addr.sin_family = AF_INET;
    server_addr.sin_port = htons(port);
    server_addr.sin_addr = *(struct in_addr*)he->h_addr_list[0];
    
    HTTP_LOG("Connecting to %s:%d...\n", 
            inet_ntoa(server_addr.sin_addr), port);
    
    if (connect(socket_fd, (struct sockaddr*)&server_addr, sizeof(server_addr)) < 0) {
        HTTP_LOG("Connection failed to %s:%d\n", host, port);
        close(socket_fd);
        socket_fd = -1;
        response.success = 0;
        return response;
    }
    
    HTTP_LOG("Connected successfully!\n");
    
    // Build HTTP request
    char request[HTTP_BUFFER_SIZE];
    const char *method_str = (method == HTTP_GET) ? "GET" : 
                             (method == HTTP_POST) ? "POST" : "PUT";
    
    snprintf(request, sizeof(request),
        "%s %s HTTP/1.0\r\n"
        "Host: %s\r\n"
        "User-Agent: NDSSyncClient/1.0\r\n"
        "X-API-Key: %s\r\n",
        method_str, path, host, api_key);
    
    if (body && body_size > 0) {
        sprintf(request + strlen(request),
            "Content-Type: %s\r\n"
            "Content-Length: %d\r\n",
            content_type ? content_type : "application/octet-stream", (int)body_size);
    }
    
    strcat(request, "Connection: close\r\n\r\n");
    
    // Send request headers
    HTTP_LOG("Sending headers...\n");
    if (send(socket_fd, request, strlen(request), 0) < 0) {
        HTTP_LOG("Failed to send request\n");
        close(socket_fd);
        socket_fd = -1;
        response.success = 0;
        return response;
    }
    
    // Send body if present
    if (body && body_size > 0) {
        HTTP_LOG("Uploading %d bytes...\n", (int)body_size);
        int total_sent = 0;
        while (total_sent < body_size) {
            int remaining = body_size - total_sent;
            int chunk = send(socket_fd, body + total_sent, remaining, 0);
            if (chunk < 0) {
                HTTP_LOG("Failed to send body\n");
                closesocket(socket_fd);
                socket_fd = -1;
                response.success = 0;
                return response;
            }
            total_sent += chunk;
            HTTP_LOG("Sent %d/%d bytes\n", total_sent, (int)body_size);
        }
        HTTP_LOG("Upload complete\n");
    }
    
    // Read response (loop until we have complete headers + body)
    HTTP_LOG("Waiting for response...\n");
    char header_buf[HTTP_BUFFER_SIZE];
    int total_received = 0;
    char *body_separator = NULL;
    int content_length = -1;
    int loop_count = 0;
    
    while (total_received < sizeof(header_buf) - 1) {
        loop_count++;
        HTTP_LOG("Loop %d: Calling recv...\n", loop_count);
        int chunk = recv(socket_fd, header_buf + total_received, sizeof(header_buf) - 1 - total_received, 0);
        HTTP_LOG("Loop %d: recv returned %d\n", loop_count, chunk);
        
        if (chunk <= 0) {
            if (total_received == 0) {
                HTTP_LOG("Failed to receive response (timeout?)\n");
                close(socket_fd);
                socket_fd = -1;
                response.success = 0;
                return response;
            }
            HTTP_LOG("recv returned %d, breaking\n", chunk);
            break;  // Got some data, proceed
        }
        total_received += chunk;
        header_buf[total_received] = '\0';
        HTTP_LOG("Total so far: %d bytes\n", total_received);
        
        // Check if we have the body separator yet
        if (!body_separator) {
            body_separator = strstr(header_buf, "\r\n\r\n");
            if (!body_separator) {
                body_separator = strstr(header_buf, "\n\n");
                if (body_separator) {
                    body_separator += 2;
                    HTTP_LOG("Found \\n\\n separator\n");
                }
            } else {
                body_separator += 4;
                HTTP_LOG("Found \\r\\n\\r\\n separator\n");
            }
        }
        
        // If we found separator, parse Content-Length
        if (body_separator && content_length < 0) {
            char *cl = strstr(header_buf, "Content-Length:");
            if (!cl) cl = strstr(header_buf, "content-length:");
            if (cl) {
                sscanf(cl + 15, " %d", &content_length);
                HTTP_LOG("Content-Length: %d\n", content_length);
            } else {
                HTTP_LOG("Content-Length header not found yet\n");
                // Don't set to 0 - keep reading to find it
            }
        }
        
        // Check if we have everything (headers + full body)
        if (body_separator && content_length >= 0) {
            int body_offset = body_separator - header_buf;
            int body_received = total_received - body_offset;
            HTTP_LOG("Body: %d/%d bytes\n", body_received, content_length);
            
            // If body is large and won't fit in buffer, break early
            if (content_length > (int)(sizeof(header_buf) - body_offset - 100)) {
                HTTP_LOG("Large body detected, breaking to read separately\n");
                break;
            }
            
            if (body_received >= content_length) {
                HTTP_LOG("Got full body, breaking\n");
                break;  // Got everything
            }
        } else if (body_separator && content_length < 0) {
            HTTP_LOG("Have separator but no Content-Length yet, keep reading\n");
        }
    }
    
    HTTP_LOG("Got %d bytes total\n", total_received);
    
    int header_len = total_received;
    
    HTTP_LOG("Parsing status...\n");
    // Parse status code
    sscanf(header_buf, "HTTP/%*d.%*d %d", &response.status_code);
    HTTP_LOG("Status: %d\n", response.status_code);
    
    HTTP_LOG("Finding body...\n");
    // Find body start (after blank line)
    char *body_start = strstr(header_buf, "\r\n\r\n");
    if (!body_start) {
        body_start = strstr(header_buf, "\n\n");
        if (body_start) {
            body_start += 2;
        }
    } else {
        body_start += 4;
    }
    
    HTTP_LOG("Extracting body...\n");
    // Calculate body size from Content-Length header
    if (body_start && content_length >= 0) {
        int body_offset = body_start - header_buf;
        int body_in_buffer = header_len - body_offset;
        
        HTTP_LOG("Content-Length: %d bytes\n", content_length);
        HTTP_LOG("Body in buffer: %d bytes\n", body_in_buffer);
        
        // Allocate memory for full body
        response.body_size = content_length;
        response.body = malloc(response.body_size + 1);
        if (!response.body) {
            HTTP_LOG("Failed to allocate %d bytes!\n", content_length);
            closesocket(socket_fd);
            socket_fd = -1;
            response.success = 0;
            return response;
        }
        
        // Copy what we already have
        if (body_in_buffer > 0) {
            int to_copy = (body_in_buffer < content_length) ? body_in_buffer : content_length;
            memcpy(response.body, body_start, to_copy);
            HTTP_LOG("Copied %d bytes from buffer\n", to_copy);
        }
        
        // Read remaining body data
        int remaining = content_length - body_in_buffer;
        int received = body_in_buffer;
        
        while (remaining > 0) {
            HTTP_LOG("Reading %d more bytes...\n", remaining);
            int chunk = recv(socket_fd, response.body + received, remaining, 0);
            if (chunk <= 0) {
                HTTP_LOG("recv failed: %d\n", chunk);
                break;
            }
            received += chunk;
            remaining -= chunk;
            HTTP_LOG("Progress: %d/%d bytes\n", received, content_length);
        }
        
        if (received == content_length) {
            HTTP_LOG("Downloaded complete: %d bytes\n", received);
            response.body[response.body_size] = '\0';
        } else {
            HTTP_LOG("Incomplete download: %d/%d\n", received, content_length);
            free(response.body);
            response.body = NULL;
            response.body_size = 0;
            closesocket(socket_fd);
            socket_fd = -1;
            response.success = 0;
            return response;
        }
    } else {
        HTTP_LOG("No body separator or Content-Length\n");
        response.body_size = 0;
    }
    
    // Read remaining response body
    // (For now, we're keeping it simple with first chunk)
    
    HTTP_LOG("Shutting down socket...\n");
    shutdown(socket_fd, 0); // SHUT_RD - like dswifi example
    HTTP_LOG("Closing socket...\n");
    closesocket(socket_fd); // Use closesocket() not close()
    socket_fd = -1;
    HTTP_LOG("Socket closed\n");
    
    HTTP_LOG("Setting success flag...\n");
    response.success = (response.status_code >= 200 && response.status_code < 300);
    HTTP_LOG("Returning response\n");
    return response;
}

HttpResponse http_request(
    const char *url,
    HttpMethod method,
    const char *api_key,
    const uint8_t *body,
    size_t body_size
) {
    return http_request_ex(url, method, api_key, "application/octet-stream", body, body_size);
}

void http_response_free(HttpResponse *response) {
    if (response->body) {
        free(response->body);
        response->body = NULL;
    }
}

void http_cleanup(void) {
    if (socket_fd >= 0) {
        close(socket_fd);
        socket_fd = -1;
    }
}
