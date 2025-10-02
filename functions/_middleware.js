/**
 * Cloudflare Pages Function Middleware
 * Runs for every request to the site.
 */
export async function onRequest(context) {
    // 1. Fetch the original response (which is your index.html or another file)
    const response = await context.next();

    // 2. Modify the response headers
    response.headers.set('X-Custom-Middleware', 'Processed-By-Pages-Function');
    response.headers.set('X-Author', 'saadsalmanakram');

    // 3. Return the modified response
    return response;
}