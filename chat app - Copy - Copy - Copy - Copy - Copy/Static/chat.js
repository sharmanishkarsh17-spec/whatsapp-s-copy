var socket = io();

function sendMessage() {
    let to = document.getElementById("to").value;
    let msg = document.getElementById("message").value;
    socket.emit("send_message", { to: to, message: msg });
    document.getElementById("message").value = "";
}

socket.on("receive_message", function(data) {
    let div = document.getElementById("messages");
    div.innerHTML += "<p><b>" + data.from + ":</b> " + data.message + "</p>";
});
