@app.route('/', methods=['GET'])
def index():
    return jsonify({'message': 'UrbanAI backend is running'})