export default {
  fetch() {
    return new Response("The Tag hosted receiver has been retired.", {status: 410});
  },
};
