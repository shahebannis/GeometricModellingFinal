An online platform that applies preprocessing techniques to pointcloud files and analyzes reconstruction qualities between different compression codecs. <br>
<br> It contains a custom implementation of normalization, MLS smoothing and resampling of the points (voxel or poisson). There is also built in compression codecs with metric collection to allow a controlled study of how geometric regularization affects compression effeciency and reconstruction quality. Only supports MPEG's G-PCC and Google's Draco. <br> <br>
The goal is to investigate how normalization, resampling and MLS smoothing of pointcloud geometry rate-distortion across different codecs 
<br> <br>
How to setup: <br>
docker compose build --no-cache app <br>
docker compose up app
